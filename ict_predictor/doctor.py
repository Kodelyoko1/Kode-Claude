"""
Preflight diagnostics for the ICT Predictor — `run_ict_predictor_auto.py --doctor`.

Answers, in one command, every question you'd otherwise have to figure out by
trial and error on a fresh Windows/VPS box:

  1. Is the MetaTrader5 package installed? (Windows-only — the usual blocker)
  2. Are MT5 credentials present in .env?
  3. Can we actually connect + log into the terminal?
  4. Is the connected account a DEMO account? (the agent refuses real money)
  5. Do our configured symbol names exist on this broker — and if not, what
     gold/oil symbols DOES this broker offer? (broker naming varies a lot:
     XAUUSD, GOLD, XAUUSD.m, GOLD.spot, …)
  6. Is the free price feed reachable?
  7. Is a killzone open right now, and if not, when's the next one?

Every check degrades gracefully — the doctor never raises, so it works
identically on a laptop with no MT5 at all and on a fully-wired VPS.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from ict_predictor import killzone, mt5_execution

# Substrings used to guess which of a broker's symbols are gold / crude oil.
_SYMBOL_HINTS = {
    "GC": ("XAU", "GOLD"),
    "CL": ("USOIL", "WTI", "CRUDE", "OIL", "XTI"),
}

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"


def _check(name: str, status: str, detail: str, fix: str = "") -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


# Category paths that mean "this is an equity/ETF, not the commodity itself".
# Matching on ticker text alone is dangerous: on MetaQuotes-Demo, "WTI" is
# W&T Offshore Inc (a stock) and "OILK" is an ETF — trading either instead of
# crude would be silently, catastrophically wrong.
_EQUITY_PATH_HINTS = ("STOCK", "ETF", "NASDAQ", "NYSE", "SHARE", "EQUIT", "BOND")


def discover_symbols(asset: str) -> list[str]:
    """Ask the connected broker which symbols plausibly ARE gold / crude oil.
    Equities and ETFs whose tickers merely contain 'OIL'/'WTI' are excluded —
    they are not the underlying commodity. Returns [] if nothing qualifies."""
    if not mt5_execution.MT5_PACKAGE_AVAILABLE:
        return []
    try:
        import MetaTrader5 as mt5
        symbols = mt5.symbols_get()
        if not symbols:
            return []
        hints = _SYMBOL_HINTS.get(asset, ())
        found = []
        for s in symbols:
            if not any(h in s.name.upper() for h in hints):
                continue
            path = (getattr(s, "path", "") or "").upper()
            if any(bad in path for bad in _EQUITY_PATH_HINTS):
                continue  # a share/ETF that happens to be named like the commodity
            desc = (getattr(s, "description", "") or "").strip()
            found.append(f"{s.name} ({desc[:38]})" if desc else s.name)
        return sorted(found)[:12]
    except Exception:
        return []


def run_diagnostics() -> list[dict]:
    checks: list[dict] = []

    # --- 1. MetaTrader5 package -------------------------------------------
    if mt5_execution.MT5_PACKAGE_AVAILABLE:
        checks.append(_check("MetaTrader5 package", OK, "installed"))
    else:
        checks.append(_check(
            "MetaTrader5 package", FAIL, "not installed",
            "Windows only. On the VPS/laptop run: pip install MetaTrader5  "
            "(it has no Linux/macOS build — order submission can't work without it, "
            "though reports still generate fine.)"))

    # --- 2. Credentials present -------------------------------------------
    missing = [v for v in ("MT5_LOGIN", "MT5_PASSWORD", "MT5_SERVER")
               if not os.getenv(v)]
    if missing:
        checks.append(_check(
            "MT5 credentials", WARN, f"missing from environment: {', '.join(missing)}",
            "Add them to .env, then run with: export $(grep -v '^#' .env | xargs)  "
            "(Windows PowerShell loads .env automatically via the setup script.)"))
    else:
        checks.append(_check("MT5 credentials", OK,
                             f"login {os.getenv('MT5_LOGIN')} @ {os.getenv('MT5_SERVER')}"))

    # --- 3. Terminal connection -------------------------------------------
    connected = False
    if mt5_execution.MT5_PACKAGE_AVAILABLE:
        connected = mt5_execution._connect()
        if connected:
            checks.append(_check("MT5 terminal connection", OK, "connected + logged in"))
        else:
            checks.append(_check(
                "MT5 terminal connection", FAIL, "could not connect / login",
                "Open the MetaTrader 5 desktop app and log into your account first — "
                "the Python API talks to a *running* terminal, it can't log in on its own."))
    else:
        checks.append(_check("MT5 terminal connection", INFO, "skipped (no MT5 package)"))

    try:
        # --- 4. Demo-account guard ----------------------------------------
        if connected:
            kind, label = mt5_execution.account_kind()
            if kind in ("demo", "contest"):
                checks.append(_check("Account type (demo-only guard)", OK, label))
            elif kind == "real":
                checks.append(_check(
                    "Account type (demo-only guard)", WARN,
                    f"{label} — orders will be BLOCKED",
                    "This agent is demo-only by design. Switch the terminal to your demo "
                    "account, or set IP_MT5_ALLOW_REAL=1 to deliberately trade real money."))
            else:
                checks.append(_check(
                    "Account type (demo-only guard)", WARN,
                    f"could not determine ({label}) — orders will be BLOCKED (fails closed)",
                    "Make sure the terminal is fully logged in and connected to the broker."))
        else:
            checks.append(_check("Account type (demo-only guard)", INFO,
                                 "skipped (not connected)"))

        # --- 4b. Algorithmic trading permission ---------------------------
        if connected:
            allowed, reason = mt5_execution.terminal_trade_allowed()
            if allowed:
                checks.append(_check("Algorithmic trading", OK, reason))
            else:
                checks.append(_check(
                    "Algorithmic trading", WARN,
                    f"{reason} — live orders will be blocked",
                    "In MT5: Tools -> Options -> Expert Advisors -> tick "
                    "'Allow algorithmic trading', then OK. (Dry-run works regardless.)"))
        else:
            checks.append(_check("Algorithmic trading", INFO, "skipped (not connected)"))

        # --- 5. Symbol resolution -----------------------------------------
        # Only check assets the agent is actually configured to trade. A broker
        # that doesn't carry crude oil is not a fault when IP_ASSETS=GC.
        configured_assets = [a.strip().upper()
                             for a in os.getenv("IP_ASSETS", "GC,CL").split(",")
                             if a.strip()]
        for asset in configured_assets:
            configured = mt5_execution.symbol_for(asset)
            if not connected:
                checks.append(_check(f"Symbol for {asset}", INFO,
                                     f"configured as '{configured}' (unverified — not connected)"))
                continue
            try:
                import MetaTrader5 as mt5
                sym = mt5.symbol_info(configured)
            except Exception:
                sym = None
            if sym is not None:
                # Existing isn't enough — confirm it's the commodity, not a
                # same-named equity/ETF, which would trade the wrong instrument.
                path = (getattr(sym, "path", "") or "").upper()
                desc = (getattr(sym, "description", "") or "").strip()
                if any(bad in path for bad in _EQUITY_PATH_HINTS):
                    checks.append(_check(
                        f"Symbol for {asset}", FAIL,
                        f"'{configured}' resolves to an equity/ETF, not {asset}: "
                        f"{desc[:50]} [{getattr(sym, 'path', '')}]",
                        f"This would trade the WRONG instrument. Pick a real "
                        f"{asset} contract, or drop {asset} from IP_ASSETS."))
                else:
                    detail = f"'{configured}' found on broker"
                    if desc:
                        detail += f" ({desc[:40]})"
                    # Selecting into Market Watch is what makes quotes and
                    # reliable tick values available; without it sizing degrades.
                    quote = ""
                    try:
                        mt5.symbol_select(configured, True)
                        tick = mt5.symbol_info_tick(configured)
                        if tick and tick.bid:
                            quote = f", bid {tick.bid:,.2f}"
                    except Exception:
                        pass
                    checks.append(_check(f"Symbol for {asset}", OK, detail + quote))
            else:
                candidates = discover_symbols(asset)
                env_var = f"IP_MT5_SYMBOL_{asset}"
                if candidates:
                    checks.append(_check(
                        f"Symbol for {asset}", FAIL,
                        f"'{configured}' not found. Broker offers: {', '.join(candidates)}",
                        f"Set {env_var}=<one of the above> in .env"))
                else:
                    checks.append(_check(
                        f"Symbol for {asset}", FAIL,
                        f"'{configured}' not found and no close match detected",
                        f"Check Market Watch in MT5 for the right name, then set {env_var} in .env"))
    finally:
        if connected:
            mt5_execution._disconnect()

    # --- 6. Price feed ------------------------------------------------------
    try:
        from ict_predictor.data_feed import get_candles, active_source
        src = active_source()
        primary = configured_assets[0] if configured_assets else "GC"
        candles = get_candles(primary, "15m")
        last = candles[-1]["c"]
        label = f"Price feed ({src})"
        if src == "mt5":
            checks.append(_check(
                label, OK,
                f"{len(candles)} {primary} 15M bars from the traded symbol, "
                f"last close {last:,.2f}"))
        else:
            checks.append(_check(
                label, WARN,
                f"{len(candles)} {primary} 15M candles from Yahoo FUTURES, "
                f"last close {last:,.2f}",
                "Yahoo quotes the futures contract; your broker quotes spot. They "
                "differ by the cost of carry, so levels computed here are indicative "
                "only and must not be submitted as orders. Connect MT5 to analyze the "
                "instrument you actually trade."))
    except Exception as exc:
        checks.append(_check(
            "Price feed", FAIL, f"unavailable: {str(exc)[:90]}",
            "Without price data the agent cannot analyze anything."))

    # --- 7. Trading mode + session -----------------------------------------
    if mt5_execution.LIVE:
        mode = "LIVE — orders will be sent to the broker"
        checks.append(_check("Order mode", WARN, mode,
                             "Unset IP_MT5_LIVE (or set 0) to return to dry-run."))
    else:
        checks.append(_check("Order mode", OK,
                             "DRY-RUN — orders computed and logged, nothing sent"))

    mode = killzone.killzone_mode()
    wins = killzone.killzone_windows_utc()
    win_txt = ", ".join(f"{n} {a:02d}:00-{b:02d}:00 UTC" for n, (a, b) in wins.items())
    if mode == "ny":
        checks.append(_check("Killzone timing", OK,
                             f"New York local time (DST-aware) — today: {win_txt}"))
    elif mode == "utc":
        checks.append(_check("Killzone timing", INFO,
                             f"fixed UTC by request (IP_KILLZONE_TZ=utc) — {win_txt}"))
    else:
        checks.append(_check(
            "Killzone timing", WARN,
            f"fell back to fixed UTC — tzdata missing, so windows run an hour "
            f"LATE during US daylight saving ({win_txt})",
            "pip install tzdata  (Windows has no system timezone database)"))

    kz = killzone.current_killzone()
    if kz:
        checks.append(_check("Killzone", OK, f"{kz} is active — signals possible now"))
    else:
        checks.append(_check("Killzone", INFO,
                             f"none active — next opens {killzone.next_killzone_open()}. "
                             f"Outside killzones every asset returns NO TRADE by design."))

    return checks


def summarize(checks: list[dict]) -> tuple[int, int]:
    fails = sum(1 for c in checks if c["status"] == FAIL)
    warns = sum(1 for c in checks if c["status"] == WARN)
    return fails, warns
