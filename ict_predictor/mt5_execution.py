"""
MetaTrader 5 order submission for the ICT Predictor.

Safety model (matches the rest of this repo's live-money agents — Media
Buyer's MB_LIVE, PolyMarket Weather's PW_LIVE_TRADING):

  - DRY-RUN BY DEFAULT. Every LONG/SHORT prediction is turned into a full
    order plan (symbol, side, volume, entry, SL, TP) whether or not MT5 is
    connected. That plan is always computed and always logged.
  - The plan is only ever sent to a broker (mt5.order_send) when
    IP_MT5_LIVE=1 is set *and* a live MT5 terminal connection succeeds.
    Anything short of that — package not installed, terminal not running,
    login failure, symbol not found — degrades to "simulated" and the
    cycle keeps going; it never raises out of tools.py.
  - DEMO ACCOUNTS ONLY. Even with IP_MT5_LIVE=1, submission is blocked
    unless the connected account reports as demo or contest. The check
    fails closed: an account we cannot positively identify as demo is
    treated as real and refused. Trading a real-money account requires
    deliberately setting IP_MT5_ALLOW_REAL=1.
  - The MetaTrader5 package is optional and Windows-only (Wine on Linux).
    Its absence never breaks report generation — only order submission.

Credentials (.env, never hardcoded):
  MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH (optional terminal path)

Env knobs:
  IP_MT5_LIVE          default "0" — set "1" to actually place orders
  IP_MT5_ALLOW_REAL    default "0" — required to trade a non-demo account
  IP_MT5_SYMBOL_GC     default "XAUUSD" — must match your broker's symbol
  IP_MT5_SYMBOL_CL     default "USOIL"  — must match your broker's symbol
  IP_MT5_RISK_PCT      default "0.5"  — % of account balance risked/trade
  IP_MT5_LOT_SIZE      default "0.01" — fallback volume when balance is unknown
  IP_MT5_MAGIC         default "990101"
  IP_MT5_DEVIATION     default "20" (points)
"""
from __future__ import annotations

import os
from typing import Optional

try:
    import MetaTrader5 as mt5
    MT5_PACKAGE_AVAILABLE = True
except ImportError:
    mt5 = None
    MT5_PACKAGE_AVAILABLE = False

LIVE = os.getenv("IP_MT5_LIVE", "0") == "1"
# Demo-only guard. The owner's standing instruction is that this agent trades
# TEST/DEMO accounts only, so live submission is additionally gated on the
# connected account actually being a demo/contest account. Placing orders on a
# real-money account requires deliberately setting IP_MT5_ALLOW_REAL=1 — the
# gate fails CLOSED, so an account we can't positively identify as demo is
# treated as real and blocked.
ALLOW_REAL = os.getenv("IP_MT5_ALLOW_REAL", "0") == "1"
SYMBOL_MAP = {
    "GC": os.getenv("IP_MT5_SYMBOL_GC", "XAUUSD"),
    "CL": os.getenv("IP_MT5_SYMBOL_CL", "USOIL"),
}
RISK_PCT = float(os.getenv("IP_MT5_RISK_PCT", "0.5")) / 100.0
FALLBACK_LOT = float(os.getenv("IP_MT5_LOT_SIZE", "0.01"))
MAGIC = int(os.getenv("IP_MT5_MAGIC", "990101"))
DEVIATION = int(os.getenv("IP_MT5_DEVIATION", "20"))


def symbol_for(asset: str) -> str:
    return SYMBOL_MAP.get(asset, asset)


def account_kind() -> tuple[str, str]:
    """
    Classify the connected MT5 account as demo / contest / real / unknown.
    Returns (kind, human_label). Requires an active connection; callers must
    treat "unknown" as unsafe (fail closed) rather than assuming demo.
    """
    if not MT5_PACKAGE_AVAILABLE:
        return "unknown", "MT5 package unavailable"
    try:
        info = mt5.account_info()
        if info is None:
            return "unknown", "account_info() returned None"
        mode = info.trade_mode
        if mode == mt5.ACCOUNT_TRADE_MODE_DEMO:
            return "demo", f"demo account {info.login} ({info.server})"
        if mode == mt5.ACCOUNT_TRADE_MODE_CONTEST:
            return "contest", f"contest account {info.login} ({info.server})"
        if mode == mt5.ACCOUNT_TRADE_MODE_REAL:
            return "real", f"REAL-MONEY account {info.login} ({info.server})"
        return "unknown", f"unrecognized trade_mode={mode}"
    except Exception as exc:
        return "unknown", f"account_info() failed: {exc}"


def _connect() -> bool:
    """Best-effort MT5 terminal connection. Returns False (never raises) on
    any failure — missing package, terminal not running, bad credentials."""
    if not MT5_PACKAGE_AVAILABLE:
        return False
    # Credentials must go to initialize() directly. Calling a bare
    # initialize() and then login() separately fails against a terminal that
    # isn't already authorized, with error -6 "Authorization failed".
    kwargs = {}
    if os.getenv("MT5_PATH"):
        kwargs["path"] = os.getenv("MT5_PATH")
    login, password, server = (os.getenv("MT5_LOGIN"), os.getenv("MT5_PASSWORD"),
                               os.getenv("MT5_SERVER"))
    if login and password and server:
        try:
            kwargs.update(login=int(login), password=password, server=server)
        except ValueError:
            return False  # non-numeric MT5_LOGIN
    try:
        return bool(mt5.initialize(**kwargs))
    except Exception:
        return False


def terminal_trade_allowed() -> tuple[bool, str]:
    """Is 'Allow algorithmic trading' enabled in the terminal? Orders are
    rejected without it, no matter how healthy everything else looks."""
    if not MT5_PACKAGE_AVAILABLE:
        return False, "MT5 package unavailable"
    try:
        info = mt5.terminal_info()
        if info is None:
            return False, "terminal_info() returned None"
        return bool(info.trade_allowed), (
            "algorithmic trading enabled" if info.trade_allowed else
            "algorithmic trading DISABLED in the terminal"
        )
    except Exception as exc:
        return False, f"terminal_info() failed: {exc}"


def _disconnect():
    if MT5_PACKAGE_AVAILABLE:
        try:
            mt5.shutdown()
        except Exception:
            pass


def _size_position(symbol: str, entry: float, invalidation: float, connected: bool) -> float:
    """Fixed-fractional position size off real account balance + symbol tick
    value when connected; otherwise the configured fallback lot size."""
    stop_distance = abs(entry - invalidation)
    if not connected or stop_distance <= 0:
        return FALLBACK_LOT
    try:
        account = mt5.account_info()
        info = mt5.symbol_info(symbol)
        if not account or not info or not info.trade_tick_size:
            return FALLBACK_LOT
        value_per_unit = info.trade_tick_value / info.trade_tick_size
        risk_amount = account.balance * RISK_PCT
        loss_per_lot = stop_distance * value_per_unit
        if loss_per_lot <= 0:
            return FALLBACK_LOT
        lots = risk_amount / loss_per_lot
        step = info.volume_step or 0.01
        lots = max(info.volume_min or step, min(info.volume_max or lots, lots))
        lots = round(lots / step) * step
        return round(lots, 2)
    except Exception:
        return FALLBACK_LOT


def _order_type_name(direction: str, entry: float, current_price: float) -> str:
    """ICT entries are retracement (limit) orders back into the FVG. Falls
    back to a stop order only if price hasn't reached the entry side yet.
    Pure string logic — works whether or not the MT5 package is installed."""
    if direction == "LONG":
        return "BUY_LIMIT" if entry <= current_price else "BUY_STOP"
    return "SELL_LIMIT" if entry >= current_price else "SELL_STOP"


_MT5_ORDER_TYPE_CONST = {
    "BUY_LIMIT": lambda: mt5.ORDER_TYPE_BUY_LIMIT,
    "BUY_STOP": lambda: mt5.ORDER_TYPE_BUY_STOP,
    "SELL_LIMIT": lambda: mt5.ORDER_TYPE_SELL_LIMIT,
    "SELL_STOP": lambda: mt5.ORDER_TYPE_SELL_STOP,
}


def _order_type_const(order_type_name: str) -> Optional[int]:
    if not MT5_PACKAGE_AVAILABLE:
        return None
    return _MT5_ORDER_TYPE_CONST[order_type_name]()


def build_order_plan(pred: dict, connected: bool = False) -> dict:
    """Pure-ish: always produces the order that *would* be placed, whether
    or not we're connected to MT5. This is what dry-run reports."""
    asset = pred["asset"]
    symbol = symbol_for(asset)
    entry = pred["entry"]
    invalidation = pred["invalidation"]
    target = pred["target"]
    current_price = pred.get("current_price", entry)
    direction = pred["direction"]

    volume = _size_position(symbol, entry, invalidation, connected)
    order_type_name = _order_type_name(direction, entry, current_price)

    return {
        "symbol": symbol,
        "direction": direction,
        "order_type": order_type_name,
        "volume": volume,
        "entry": entry,
        "sl": invalidation,
        "tp": target,
        "magic": MAGIC,
        "deviation": DEVIATION,
        "comment": f"ICT {asset} {direction} auto",
    }


def submit(pred: dict) -> dict:
    """
    Turn a LONG/SHORT prediction into an MT5 order. Always returns a result
    dict with a 'status' — never raises, so one broker hiccup can't take
    down the rest of the cycle.

    status ∈ {"simulated", "submitted", "mt5_unavailable", "connect_failed",
              "symbol_not_found", "rejected", "error"}
    """
    if pred.get("direction") not in ("LONG", "SHORT"):
        return {"status": "skipped", "reason": "no trade signal"}

    if not MT5_PACKAGE_AVAILABLE:
        plan = build_order_plan(pred, connected=False)
        return {
            "status": "mt5_unavailable" if LIVE else "simulated",
            "live": False,
            "plan": plan,
            "note": "MetaTrader5 package not installed — install it on a "
                    "Windows host (or under Wine) to enable submission.",
        }

    connected = _connect()
    try:
        # Select the symbol into Market Watch BEFORE sizing. MT5 returns no
        # tick data (and unreliable tick value/size) for unselected symbols,
        # which would silently push _size_position onto its fallback lot.
        if connected:
            try:
                mt5.symbol_select(symbol_for(pred["asset"]), True)
            except Exception:
                pass

        plan = build_order_plan(pred, connected=connected)

        if not LIVE:
            return {"status": "simulated", "live": False, "plan": plan,
                    "note": "IP_MT5_LIVE not set — order computed but not sent."}

        if not connected:
            return {"status": "connect_failed", "live": True, "plan": plan,
                    "note": "Could not connect/login to the MT5 terminal."}

        # Demo-only guard — fails closed on anything not positively demo/contest.
        kind, label = account_kind()
        if kind not in ("demo", "contest") and not ALLOW_REAL:
            return {
                "status": "real_account_blocked", "live": True, "plan": plan,
                "account_kind": kind,
                "note": f"Refusing to trade: connected to {label}. This agent is "
                        f"configured for demo/test accounts only. Set "
                        f"IP_MT5_ALLOW_REAL=1 to deliberately override.",
            }

        # Never submit levels derived from a different instrument. Yahoo
        # quotes COMEX futures; the broker quotes spot. Orders priced off
        # futures land on the wrong side of the spot market.
        if pred.get("price_source") == "yahoo":
            return {
                "status": "wrong_instrument", "live": True, "plan": plan,
                "note": "Levels were computed from Yahoo FUTURES data but this "
                        "order would trade the broker's SPOT symbol. Those prices "
                        "differ by the cost of carry. Connect MT5 so analysis and "
                        "execution use the same instrument.",
            }

        allowed, reason = terminal_trade_allowed()
        if not allowed:
            return {
                "status": "algo_trading_disabled", "live": True, "plan": plan,
                "note": f"{reason}. Enable it in MT5: Tools -> Options -> "
                        f"Expert Advisors -> 'Allow algorithmic trading'.",
            }

        info = mt5.symbol_info(plan["symbol"])
        if info is None or not mt5.symbol_select(plan["symbol"], True):
            return {"status": "symbol_not_found", "live": True, "plan": plan,
                    "note": f"Symbol '{plan['symbol']}' not found on this broker — "
                            f"check IP_MT5_SYMBOL_GC / IP_MT5_SYMBOL_CL."}

        order_type = _order_type_const(plan["order_type"])
        request = {
            "action": mt5.TRADE_ACTION_PENDING,
            "symbol": plan["symbol"],
            "volume": plan["volume"],
            "type": order_type,
            "price": plan["entry"],
            "sl": plan["sl"],
            "tp": plan["tp"],
            "deviation": plan["deviation"],
            "magic": plan["magic"],
            "comment": plan["comment"],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_RETURN,
        }
        result = mt5.order_send(request)
        if result is None:
            return {"status": "error", "live": True, "plan": plan,
                    "note": f"order_send returned None: {mt5.last_error()}"}
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            return {"status": "rejected", "live": True, "plan": plan,
                    "retcode": result.retcode, "note": result.comment}

        return {"status": "submitted", "live": True, "plan": plan,
                "ticket": result.order, "note": "Order placed on MT5."}
    except Exception as exc:
        return {"status": "error", "live": LIVE, "note": str(exc)}
    finally:
        _disconnect()
