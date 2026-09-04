"""
Central instrument registry for the ICT Predictor.

Every other module in this package (data_feed, mt5_execution, killzone,
predictor, report, backtest) reads instrument metadata from HERE instead of
hardcoding per-asset special cases. Adding a new tradable instrument is a
matter of adding one row below — nothing else in the codebase should need
touching.

Fields per instrument:
  label              human-readable name for reports
  yahoo              Yahoo Finance chart symbol (fallback price feed)
  mt5_symbol_default default MT5 broker symbol — overridable per-asset via
                     IP_MT5_SYMBOL_<ASSET> (see mt5_execution.py), since
                     brokers vary naming (e.g. a raw-spread account suffixing
                     "EURUSD.a" or "EURUSDm")
  decimals           display/rounding precision. Gold/oil trade in 2-decimal
                     dollar terms; most FX pairs quote 5 decimals (a pip is
                     the 4th, the 5th is a "pipette"); JPY-quoted pairs quote
                     only 3 (a pip is the 2nd decimal)
  killzones          which of the two ICT killzones this instrument is
                     evaluated in (see killzone.ASSET_KILLZONE_FIT)
  bt_spread/bt_slippage/bt_swap_per_night
                     backtest cost-model defaults, in PRICE UNITS of the
                     instrument (see backtest.py) — these are wildly
                     different scales for a $4,300 gold quote vs a 1.08 FX
                     quote, so each instrument carries its own. All three
                     remain overridable globally via IP_BT_SPREAD /
                     IP_BT_SLIPPAGE / IP_BT_SWAP for anyone who wants to
                     force one number across every asset.

Not financial advice. Added instruments trade exactly the same
sweep -> displacement/MSS -> FVG rule set as Gold/Crude; nothing about the
strategy logic changes per instrument, only these metadata knobs.
"""
from __future__ import annotations

_LONDON = "London Killzone"
_NY_AM = "NY AM Killzone"

INSTRUMENTS: dict[str, dict] = {
    # --- Metals / energy (original spec) ---------------------------------
    "GC": {
        "label": "GC (Gold)",
        "yahoo": "GC=F",
        "mt5_symbol_default": "XAUUSD",
        "decimals": 2,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.30,
        "bt_slippage": 0.10,
        "bt_swap_per_night": 0.15,
    },
    "CL": {
        "label": "CL (Crude Oil)",
        "yahoo": "CL=F",
        "mt5_symbol_default": "USOIL",
        "decimals": 2,
        "killzones": {_NY_AM},
        "bt_spread": 0.30,
        "bt_slippage": 0.10,
        "bt_swap_per_night": 0.15,
    },

    # --- FX majors (5-decimal quoting) ------------------------------------
    "EURUSD": {
        "label": "EUR/USD",
        "yahoo": "EURUSD=X",
        "mt5_symbol_default": "EURUSD",
        "decimals": 5,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.00012,
        "bt_slippage": 0.00005,
        "bt_swap_per_night": 0.00004,
    },
    "GBPUSD": {
        "label": "GBP/USD",
        "yahoo": "GBPUSD=X",
        "mt5_symbol_default": "GBPUSD",
        "decimals": 5,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.00018,
        "bt_slippage": 0.00007,
        "bt_swap_per_night": 0.00005,
    },
    "AUDUSD": {
        "label": "AUD/USD",
        "yahoo": "AUDUSD=X",
        "mt5_symbol_default": "AUDUSD",
        "decimals": 5,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.00015,
        "bt_slippage": 0.00006,
        "bt_swap_per_night": 0.00004,
    },
    "NZDUSD": {
        "label": "NZD/USD",
        "yahoo": "NZDUSD=X",
        "mt5_symbol_default": "NZDUSD",
        "decimals": 5,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.00020,
        "bt_slippage": 0.00008,
        "bt_swap_per_night": 0.00004,
    },
    "USDCAD": {
        "label": "USD/CAD",
        "yahoo": "USDCAD=X",
        "mt5_symbol_default": "USDCAD",
        "decimals": 5,
        "killzones": {_NY_AM},
        "bt_spread": 0.00018,
        "bt_slippage": 0.00007,
        "bt_swap_per_night": 0.00004,
    },
    "USDCHF": {
        "label": "USD/CHF",
        "yahoo": "USDCHF=X",
        "mt5_symbol_default": "USDCHF",
        "decimals": 5,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.00018,
        "bt_slippage": 0.00007,
        "bt_swap_per_night": 0.00004,
    },

    # --- JPY-quoted pairs (3-decimal quoting, pip = 0.01) -----------------
    "USDJPY": {
        "label": "USD/JPY",
        "yahoo": "USDJPY=X",
        "mt5_symbol_default": "USDJPY",
        "decimals": 3,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.015,
        "bt_slippage": 0.006,
        "bt_swap_per_night": 0.004,
    },
    "EURJPY": {
        "label": "EUR/JPY",
        "yahoo": "EURJPY=X",
        "mt5_symbol_default": "EURJPY",
        "decimals": 3,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.020,
        "bt_slippage": 0.008,
        "bt_swap_per_night": 0.005,
    },
    "GBPJPY": {
        "label": "GBP/JPY",
        "yahoo": "GBPJPY=X",
        "mt5_symbol_default": "GBPJPY",
        "decimals": 3,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.030,
        "bt_slippage": 0.012,
        "bt_swap_per_night": 0.006,
    },
}


def get(asset: str) -> dict:
    """Metadata for `asset`, or a safe 2-decimal/no-killzone-restriction
    default for anything not in the registry yet (so a typo'd IP_ASSETS
    entry degrades gracefully instead of raising deep inside some module
    that assumed every asset is registered)."""
    return INSTRUMENTS.get(asset.upper(), {
        "label": asset,
        "yahoo": None,
        "mt5_symbol_default": asset,
        "decimals": 2,
        "killzones": {_LONDON, _NY_AM},
        "bt_spread": 0.0,
        "bt_slippage": 0.0,
        "bt_swap_per_night": 0.0,
    })


def decimals_for(asset: str) -> int:
    return get(asset).get("decimals", 2)


def label_for(asset: str) -> str:
    return get(asset).get("label", asset)
