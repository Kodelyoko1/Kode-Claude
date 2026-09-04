# ICT Silver Bullet / Killzone — NinjaTrader 8 Strategy

A fully automated NinjaScript (C#) strategy implementing the ICT Silver
Bullet / Killzone model: liquidity sweep → displacement Market Structure
Shift (MSS) → 3-candle Fair Value Gap (FVG) entry, sized to a fixed % of
account equity, restricted to the London and New York AM killzones.

This is a standalone NinjaTrader 8 add-on — separate from the Python
`ict_predictor/` agent elsewhere in this repo (which targets MetaTrader 5).
Use whichever platform you actually trade on; the underlying ICT logic is
the same model expressed twice, once in Python/MT5 and once here in
NinjaScript/NT8.

## What it does

1. **Killzones** — entries are only considered inside the London
   (02:00–05:00 Eastern) and New York AM (08:30–11:00 Eastern) windows.
   Bar timestamps are converted from the chart's trading-hours time zone to
   `America/New_York` (via the Windows "Eastern Standard Time" zone, which
   auto-handles the EST/EDT switch), so the windows stay correct year
   round. Any open position or working order is flattened/cancelled the
   moment the active killzone ends.
2. **Liquidity mapping** — a higher-timeframe series (15 minute by default,
   added internally via `AddDataSeries`) is scanned for confirmed swing
   highs/lows using an N-bar fractal test. Swing highs become resting
   Buy-Side Liquidity (BSL) pools, swing lows become Sell-Side Liquidity
   (SSL) pools, each tracked until price trades through it.
3. **Sweep → MSS** — on the chart's own (precision/entry) timeframe, once a
   pool is swept during an active killzone, the strategy waits for a
   strong-bodied displacement candle (body ≥ `DisplacementAtrMultiple ×
   ATR`) in the opposite direction that closes beyond the most recent minor
   swing point — that's the Market Structure Shift.
4. **3-candle FVG entry** — the gap left by the displacement leg is located
   and a limit order is placed at either the FVG's proximal boundary or its
   50% midpoint (Consequent Encroachment), your choice via `EntryMode`.
5. **Risk management** — the stop sits beyond the wick extreme of the whole
   displacement leg (plus a small tick buffer). Position size is solved so
   the stop distance risks exactly `RiskPercentPerTrade` of account equity
   (via `Account.Get(AccountItem.CashValue, ...)`, capped at
   `MaxContracts`). The target is either a fixed R multiple
   (`RewardRiskMultiple`) or the nearest untouched opposing liquidity pool
   that still clears `MinRewardRisk`, whichever you enable. A
   `DailyMaxLossPercent` cap halts new entries and flattens for the rest of
   the session once hit.

## Install

1. Open NinjaTrader 8 → **Tools → Edit NinjaScript → Strategy... → New...**
   (or copy `ICTSilverBulletKillzone.cs` directly into
   `Documents\NinjaTrader 8\bin\Custom\Strategies\`).
2. Paste in the contents of `ICTSilverBulletKillzone.cs`, replacing the
   generated boilerplate.
3. Press **F5** (Compile). Fix any editor-flagged issues if your NT8 build
   differs slightly in API surface (see notes below).
4. Apply the strategy to a 1–5 minute chart of a liquid futures or FX
   instrument (e.g. GC, CL, ES, 6E) with enough historical bars loaded to
   satisfy `BarsRequiredToTrade` (default 30) plus the higher-timeframe
   series warm-up.
5. **Always validate on Sim/Playback/Strategy Analyzer first.** This is a
   decision-support automation template, not financial advice, and carries
   substantial risk of loss when connected to a live account.

## Key inputs

| Group | Input | Default | Notes |
|---|---|---|---|
| Killzones | `UseLondonKillzone` / `LondonStart` / `LondonEnd` | on / 02:00 / 05:00 ET | |
| Killzones | `UseNyAmKillzone` / `NyAmStart` / `NyAmEnd` | on / 08:30 / 11:00 ET | |
| Structure | `HtfMinutes` | 15 | higher-timeframe bar size for BSL/SSL pools |
| Structure | `HtfSwingStrength` / `LtfSwingStrength` | 3 / 3 | fractal bars-each-side for swing confirmation |
| Structure | `DisplacementAtrMultiple` | 1.3 | how strong the MSS candle's body must be vs ATR |
| Execution | `EntryMode` | ProximalBoundary | or `Midpoint` (CE) |
| Execution | `RewardRiskMultiple` / `MinRewardRisk` | 2.0 / 2.0 | fixed R target / floor for any target |
| Execution | `UseOpposingLiquidityTarget` | true | prefer nearest valid opposing pool over the fixed R target |
| Risk | `RiskPercentPerTrade` | 1.0 | % of equity risked per trade |
| Risk | `DailyMaxLossPercent` | 3.0 | halts new entries + flattens for the day once hit |
| Risk | `MaxContracts` | 10 | hard cap regardless of computed size |
| Diagnostics | `PrintDebug` / `ShowDrawings` | off / on | Output-window logging and chart annotations |

## Notes / assumptions

- Written against the NinjaTrader 8 NinjaScript API surface as of the 8.1
  strategy template (`Strategy` base, managed orders via
  `EnterLongLimit`/`SetStopLoss`/`SetProfitTarget`). If your installed NT8
  build has renamed/deprecated any of the enum members used in
  `State.SetDefaults` (e.g. `RealtimeErrorHandling`,
  `MaximumBarsLookBack`), the editor's red squiggles will point at the
  exact line — these are stable, long-standing NT8 APIs so this should be
  rare.
- Daily loss tracking uses the account's cash-value delta since the first
  bar of the session as a proxy for realized P&L. If you run other
  strategies or manual trades on the same account simultaneously, that
  delta will include their P&L too.
- `TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time")` uses the
  Windows time-zone database (NinjaTrader 8 is Windows-only), consistent
  with how the rest of this repo's Windows/MT5 tooling handles US time
  zones.
- Liquidity pools are only marked "swept" while a killzone is active (since
  that's the only time this strategy looks at price), not on out-of-window
  bars.
