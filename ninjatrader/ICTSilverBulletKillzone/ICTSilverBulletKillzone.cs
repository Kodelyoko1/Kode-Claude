#region Using declarations
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.ComponentModel.DataAnnotations;
using System.Windows.Media;
using NinjaTrader.Cbi;
using NinjaTrader.Gui.Tools;
using NinjaTrader.NinjaScript;
using NinjaTrader.NinjaScript.Indicators;
using NinjaTrader.NinjaScript.DrawingTools;
#endregion

// ---------------------------------------------------------------------------
// ICT Silver Bullet / Killzone automated strategy for NinjaTrader 8.
//
// Model implemented (pure price action, no repainting indicators):
//   1. Only look for trades inside the London (02:00-05:00 America/New_York)
//      or New York AM (08:30-11:00 America/New_York) killzones. Everything
//      is converted to Eastern time internally so it stays correct across
//      both EST and EDT.
//   2. A higher-timeframe series (default 15 minute, added with
//      AddDataSeries) is scanned for confirmed swing highs/lows. Swing highs
//      become unswept Buy-Side Liquidity (BSL) pools, swing lows become
//      unswept Sell-Side Liquidity (SSL) pools.
//   3. On the chart (precision/entry) timeframe, once a killzone is active,
//      the strategy waits for price to run one of those pools (a
//      liquidity sweep).
//   4. After the sweep it waits for a strong-bodied displacement candle in
//      the opposite direction that breaks the most recent minor swing point
//      on the entry timeframe -> Market Structure Shift (MSS).
//   5. The 3-candle Fair Value Gap (FVG) created by that displacement leg is
//      located and a limit order is placed at the FVG's proximal boundary
//      or its 50% midpoint (Consequent Encroachment), per user choice.
//   6. Stop loss sits beyond the wick extreme of the whole displacement leg.
//      Size is calculated so the stop distance risks exactly
//      RiskPercentPerTrade of account equity. Target is either a fixed R
//      multiple or the nearest untouched opposing liquidity pool.
//   7. Any open position/working order is flattened the moment the active
//      killzone window ends. A daily loss cap halts new entries (and
//      flattens) for the rest of the session once hit.
//
// Drop this file into Documents\NinjaTrader 8\bin\Custom\Strategies\ (or
// paste it into a new file created from the NinjaScript Editor) and compile
// (F5). Apply to a 1-5 minute chart of GC or CL (or any liquid futures/FX
// instrument) with the default 15 minute higher-timeframe series.
//
// This is a decision-support / automation template, not financial advice.
// Backtest and forward-test on a demo/sim account before ever going live.
// ---------------------------------------------------------------------------

namespace NinjaTrader.NinjaScript.Strategies
{
    public enum FvgEntryMode
    {
        Midpoint,
        ProximalBoundary
    }

    public enum LiquidityPoolType
    {
        BuySide,
        SellSide
    }

    /// <summary>
    /// One confirmed higher-timeframe swing point acting as a resting
    /// liquidity pool until price trades through it (or it ages out).
    /// </summary>
    public class LiquidityPool
    {
        public double Price;
        public DateTime FormedTime;
        public LiquidityPoolType Type;
        public bool Swept;
    }

    public enum SetupState
    {
        Idle,
        AwaitingMss,
        AwaitingFvg,
        OrderPending,
        InPosition
    }

    public enum TradeDirection
    {
        None,
        Bullish,
        Bearish
    }

    public class ICTSilverBulletKillzone : Strategy
    {
        #region Variables

        // -- higher-timeframe liquidity pools --
        private readonly List<LiquidityPool> bslPools = new List<LiquidityPool>(); // buy-side (above swing highs)
        private readonly List<LiquidityPool> sslPools = new List<LiquidityPool>(); // sell-side (below swing lows)
        private const int MaxPoolsTracked = 25;

        // -- entry-timeframe minor swing tracker (used for MSS confirmation) --
        private double lastMinorSwingHigh = double.NaN;
        private double lastMinorSwingLow = double.NaN;

        // -- state machine --
        private SetupState state = SetupState.Idle;
        private TradeDirection setupDirection = TradeDirection.None;
        private int sweepBarIndex = -1;
        private int mssBarIndex = -1;
        private double legHigh = double.MinValue;
        private double legLow = double.MaxValue;
        private double sweptPoolPrice = double.NaN;

        private double pendingEntryPrice;
        private double pendingStopPrice;
        private double pendingTargetPrice;
        private int pendingQuantity;

        private Order entryOrderRef;
        private string activeSignalName = string.Empty;

        // -- risk / day state --
        private DateTime currentSessionDate = DateTime.MinValue;
        private double dayStartEquity;
        private bool dailyLossLockout;

        // -- indicators --
        private ATR atr;

        // -- time zone helper --
        private static readonly TimeZoneInfo EasternZone =
            TimeZoneInfo.FindSystemTimeZoneById("Eastern Standard Time");

        #endregion

        #region Properties (user inputs)

        [NinjaScriptProperty]
        [Display(Name = "Trade London Killzone", Order = 1, GroupName = "1. Killzones (Eastern Time)")]
        public bool UseLondonKillzone { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "London Start (ET)", Order = 2, GroupName = "1. Killzones (Eastern Time)")]
        public TimeSpan LondonStart { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "London End (ET)", Order = 3, GroupName = "1. Killzones (Eastern Time)")]
        public TimeSpan LondonEnd { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Trade NY AM Killzone", Order = 4, GroupName = "1. Killzones (Eastern Time)")]
        public bool UseNyAmKillzone { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "NY AM Start (ET)", Order = 5, GroupName = "1. Killzones (Eastern Time)")]
        public TimeSpan NyAmStart { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "NY AM End (ET)", Order = 6, GroupName = "1. Killzones (Eastern Time)")]
        public TimeSpan NyAmEnd { get; set; }

        [NinjaScriptProperty]
        [Range(1, int.MaxValue)]
        [Display(Name = "HTF minutes (liquidity pools)", Order = 1, GroupName = "2. Liquidity / Structure")]
        public int HtfMinutes { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "HTF swing strength (bars each side)", Order = 2, GroupName = "2. Liquidity / Structure")]
        public int HtfSwingStrength { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Entry-TF minor swing strength", Order = 3, GroupName = "2. Liquidity / Structure")]
        public int LtfSwingStrength { get; set; }

        [NinjaScriptProperty]
        [Range(1, 500)]
        [Display(Name = "ATR period (displacement filter)", Order = 4, GroupName = "2. Liquidity / Structure")]
        public int AtrPeriod { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 10.0)]
        [Display(Name = "Displacement ATR multiple", Order = 5, GroupName = "2. Liquidity / Structure")]
        public double DisplacementAtrMultiple { get; set; }

        [NinjaScriptProperty]
        [Range(1, 200)]
        [Display(Name = "Max bars to wait for MSS after sweep", Order = 6, GroupName = "2. Liquidity / Structure")]
        public int MaxBarsForMss { get; set; }

        [NinjaScriptProperty]
        [Range(1, 20)]
        [Display(Name = "Max bars to wait for FVG after MSS", Order = 7, GroupName = "2. Liquidity / Structure")]
        public int MaxBarsForFvg { get; set; }

        [NinjaScriptProperty]
        [Range(1, 500)]
        [Display(Name = "Max bars to wait for limit fill", Order = 8, GroupName = "2. Liquidity / Structure")]
        public int MaxBarsForFill { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "FVG entry mode", Order = 1, GroupName = "3. Execution")]
        public FvgEntryMode EntryMode { get; set; }

        [NinjaScriptProperty]
        [Range(0, 100)]
        [Display(Name = "Stop buffer (ticks beyond wick)", Order = 2, GroupName = "3. Execution")]
        public int StopBufferTicks { get; set; }

        [NinjaScriptProperty]
        [Range(0.1, 20.0)]
        [Display(Name = "Fixed reward:risk multiple", Order = 3, GroupName = "3. Execution")]
        public double RewardRiskMultiple { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Target opposing liquidity pool when available", Order = 4, GroupName = "3. Execution")]
        public bool UseOpposingLiquidityTarget { get; set; }

        [NinjaScriptProperty]
        [Range(1.0, 10.0)]
        [Display(Name = "Min R:R required to trade", Order = 5, GroupName = "3. Execution")]
        public double MinRewardRisk { get; set; }

        [NinjaScriptProperty]
        [Range(0.01, 100.0)]
        [Display(Name = "Risk % of equity per trade", Order = 1, GroupName = "4. Risk Management")]
        public double RiskPercentPerTrade { get; set; }

        [NinjaScriptProperty]
        [Range(0.01, 100.0)]
        [Display(Name = "Daily max loss % (stop trading)", Order = 2, GroupName = "4. Risk Management")]
        public double DailyMaxLossPercent { get; set; }

        [NinjaScriptProperty]
        [Range(1, 10000)]
        [Display(Name = "Max contracts/lots per trade", Order = 3, GroupName = "4. Risk Management")]
        public int MaxContracts { get; set; }

        [NinjaScriptProperty]
        [Range(1, double.MaxValue)]
        [Display(Name = "Fallback equity (if account unavailable)", Order = 4, GroupName = "4. Risk Management")]
        public double FallbackEquity { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Print debug info", Order = 1, GroupName = "5. Diagnostics")]
        public bool PrintDebug { get; set; }

        [NinjaScriptProperty]
        [Display(Name = "Draw sweep / FVG / order levels on chart", Order = 2, GroupName = "5. Diagnostics")]
        public bool ShowDrawings { get; set; }

        #endregion

        protected override void OnStateChange()
        {
            if (State == State.SetDefaults)
            {
                Description = "ICT Silver Bullet / Killzone automated strategy: liquidity sweep -> " +
                               "displacement MSS -> 3-candle FVG entry, sized to a fixed % account risk, " +
                               "restricted to the London and NY AM killzones with a daily loss cap.";
                Name = "ICTSilverBulletKillzone";
                Calculate = Calculate.OnBarClose;
                EntriesPerDirection = 1;
                EntryHandling = EntryHandling.UniqueEntries;
                IsExitOnSessionCloseStrategy = true;
                ExitOnSessionCloseSeconds = 30;
                IsFillLimitOnTouch = false;
                MaximumBarsLookBack = MaximumBarsLookBack.TwoHundredFiftySix;
                OrderFillResolution = OrderFillResolution.Standard;
                Slippage = 0;
                StartBehavior = StartBehavior.WaitUntilFlat;
                TimeInForce = TimeInForce.Gtc;
                TraceOrders = false;
                RealtimeErrorHandling = RealtimeErrorHandling.StopCancelClose;
                StopTargetHandling = StopTargetHandling.PerEntryExecution;
                BarsRequiredToTrade = 30;
                IsInstantiatedOnEachOptimizationIteration = true;
                IsUnmanaged = false;

                // -- default inputs, matching the spec in the prompt --
                UseLondonKillzone = true;
                LondonStart = new TimeSpan(2, 0, 0);
                LondonEnd = new TimeSpan(5, 0, 0);
                UseNyAmKillzone = true;
                NyAmStart = new TimeSpan(8, 30, 0);
                NyAmEnd = new TimeSpan(11, 0, 0);

                HtfMinutes = 15;
                HtfSwingStrength = 3;
                LtfSwingStrength = 3;
                AtrPeriod = 14;
                DisplacementAtrMultiple = 1.3;
                MaxBarsForMss = 20;
                MaxBarsForFvg = 3;
                MaxBarsForFill = 15;

                EntryMode = FvgEntryMode.ProximalBoundary;
                StopBufferTicks = 2;
                RewardRiskMultiple = 2.0;
                UseOpposingLiquidityTarget = true;
                MinRewardRisk = 2.0;

                RiskPercentPerTrade = 1.0;
                DailyMaxLossPercent = 3.0;
                MaxContracts = 10;
                FallbackEquity = 50000;

                PrintDebug = false;
                ShowDrawings = true;
            }
            else if (State == State.Configure)
            {
                // BarsArray[1] = higher-timeframe series used only to build
                // the BSL/SSL liquidity pool map.
                AddDataSeries(BarsPeriodType.Minute, HtfMinutes);
            }
            else if (State == State.DataLoaded)
            {
                atr = ATR(AtrPeriod);
                ResetDailyState(true);
            }
            else if (State == State.Terminated)
            {
                entryOrderRef = null;
            }
        }

        protected override void OnBarUpdate()
        {
            // Higher-timeframe series: only used to refresh the liquidity map.
            if (BarsInProgress == 1)
            {
                UpdateHtfLiquidityPools();
                return;
            }

            if (BarsInProgress != 0)
                return;

            if (CurrentBar < BarsRequiredToTrade)
                return;

            // ---- daily bookkeeping ----
            if (Bars.IsFirstBarOfSession)
                ResetDailyState(false);

            CheckDailyLossCap();

            // ---- update entry-timeframe minor swing points (for MSS refs) ----
            UpdateMinorSwings();

            // ---- killzone gating ----
            DateTime et = ToEasternTime(Time[0]);
            bool inKillzone = IsInKillzone(et);

            if (!inKillzone)
            {
                // Killzone window closed (or never opened today) -> make sure
                // we are flat and any pending setup/order is cleared out.
                if (state != SetupState.Idle)
                {
                    FlattenAndReset("killzone window ended");
                }
                return;
            }

            if (dailyLossLockout)
            {
                // Daily max loss hit -> no new risk, but let an existing
                // managed position ride its own stop/target rather than
                // panic-closing mid-trade. New setups are blocked below.
                if (state == SetupState.AwaitingMss || state == SetupState.AwaitingFvg ||
                    state == SetupState.OrderPending)
                {
                    FlattenAndReset("daily loss cap hit");
                }
                return;
            }

            // Always keep the liquidity map current: a pool must be flagged
            // Swept the instant price trades through it, even if we are mid
            // way through managing a different setup and can't act on it.
            // Only an Idle state machine actually reacts to a fresh sweep.
            TradeDirection freshSweepDir;
            LiquidityPool freshSweep = MarkAndDetectSweeps(out freshSweepDir);

            // ---- run the setup state machine ----
            switch (state)
            {
                case SetupState.Idle:
                    if (freshSweep != null)
                        StartSetupFromSweep(freshSweep, freshSweepDir);
                    break;

                case SetupState.AwaitingMss:
                    LookForDisplacementMss();
                    break;

                case SetupState.AwaitingFvg:
                    LookForFairValueGap();
                    break;

                case SetupState.OrderPending:
                    ManagePendingOrder();
                    break;

                case SetupState.InPosition:
                    if (Position.MarketPosition == MarketPosition.Flat)
                    {
                        Log("Position closed (stop/target hit) - resetting for next setup.");
                        ResetSetup();
                    }
                    break;
            }
        }

        #region Liquidity map (higher timeframe)

        private void UpdateHtfLiquidityPools()
        {
            double price;
            DateTime t;

            if (TryGetPivotHigh(HtfSwingStrength, out price, out t))
                AddPool(bslPools, LiquidityPoolType.BuySide, price, t);

            if (TryGetPivotLow(HtfSwingStrength, out price, out t))
                AddPool(sslPools, LiquidityPoolType.SellSide, price, t);
        }

        private void AddPool(List<LiquidityPool> pools, LiquidityPoolType type, double price, DateTime t)
        {
            // Avoid duplicate pivots (same bar re-detected).
            for (int i = 0; i < pools.Count; i++)
            {
                if (pools[i].FormedTime == t)
                    return;
            }

            pools.Add(new LiquidityPool { Price = price, FormedTime = t, Type = type, Swept = false });

            if (pools.Count > MaxPoolsTracked)
                pools.RemoveAt(0);

            if (ShowDrawings)
            {
                string tag = (type == LiquidityPoolType.BuySide ? "BSL_" : "SSL_") + t.Ticks;
                Draw.HorizontalLine(this, tag, price,
                    type == LiquidityPoolType.BuySide ? Brushes.DarkOrange : Brushes.DodgerBlue);
            }
        }

        #endregion

        #region Minor swing tracking (entry timeframe, for MSS reference)

        private void UpdateMinorSwings()
        {
            double price;
            DateTime t;

            if (TryGetPivotHigh(LtfSwingStrength, out price, out t))
                lastMinorSwingHigh = price;

            if (TryGetPivotLow(LtfSwingStrength, out price, out t))
                lastMinorSwingLow = price;
        }

        // Generic fractal pivot-high test: bar `strength` bars ago must be a
        // strictly higher high than `strength` bars on either side of it.
        private bool TryGetPivotHigh(int strength, out double price, out DateTime time)
        {
            price = double.NaN;
            time = Time[0];

            if (CurrentBars[BarsInProgress] < 2 * strength)
                return false;

            int pivot = strength;
            double candidate = High[pivot];

            for (int i = 1; i <= strength; i++)
            {
                if (High[pivot - i] >= candidate || High[pivot + i] >= candidate)
                    return false;
            }

            price = candidate;
            time = Time[pivot];
            return true;
        }

        // Same as above for swing lows.
        private bool TryGetPivotLow(int strength, out double price, out DateTime time)
        {
            price = double.NaN;
            time = Time[0];

            if (CurrentBars[BarsInProgress] < 2 * strength)
                return false;

            int pivot = strength;
            double candidate = Low[pivot];

            for (int i = 1; i <= strength; i++)
            {
                if (Low[pivot - i] <= candidate || Low[pivot + i] <= candidate)
                    return false;
            }

            price = candidate;
            time = Time[pivot];
            return true;
        }

        #endregion

        #region State machine steps

        // Marks every pool price has traded through this bar as Swept (so the
        // liquidity map never goes stale) and returns the nearest freshly
        // swept pool, if any, for the Idle state to react to. Runs every bar
        // regardless of state.
        private LiquidityPool MarkAndDetectSweeps(out TradeDirection dir)
        {
            // Sweep of buy-side liquidity (a resting swing high taken out) ->
            // hunting a bearish reversal (MSS to the downside).
            LiquidityPool nearestBsl = null;
            for (int i = 0; i < bslPools.Count; i++)
            {
                LiquidityPool p = bslPools[i];
                if (p.Swept || High[0] <= p.Price)
                    continue;

                p.Swept = true;
                if (nearestBsl == null || p.Price < nearestBsl.Price)
                    nearestBsl = p;
            }

            // Sweep of sell-side liquidity (a resting swing low taken out) ->
            // hunting a bullish reversal (MSS to the upside).
            LiquidityPool nearestSsl = null;
            for (int i = 0; i < sslPools.Count; i++)
            {
                LiquidityPool p = sslPools[i];
                if (p.Swept || Low[0] >= p.Price)
                    continue;

                p.Swept = true;
                if (nearestSsl == null || p.Price > nearestSsl.Price)
                    nearestSsl = p;
            }

            // If both somehow trigger on the same bar (wide range bar),
            // prefer whichever pool is closer to the current close.
            dir = TradeDirection.None;

            if (nearestBsl != null && nearestSsl != null)
            {
                double distBsl = Math.Abs(Close[0] - nearestBsl.Price);
                double distSsl = Math.Abs(Close[0] - nearestSsl.Price);
                if (distBsl <= distSsl) { dir = TradeDirection.Bearish; return nearestBsl; }
                dir = TradeDirection.Bullish;
                return nearestSsl;
            }

            if (nearestBsl != null) { dir = TradeDirection.Bearish; return nearestBsl; }
            if (nearestSsl != null) { dir = TradeDirection.Bullish; return nearestSsl; }

            return null;
        }

        private void StartSetupFromSweep(LiquidityPool sweptPool, TradeDirection dir)
        {
            setupDirection = dir;
            sweepBarIndex = CurrentBar;
            sweptPoolPrice = sweptPool.Price;
            legHigh = High[0];
            legLow = Low[0];
            state = SetupState.AwaitingMss;

            Log(string.Format("Swept {0} liquidity @ {1} on bar {2}. Awaiting {3} MSS.",
                dir == TradeDirection.Bearish ? "BSL (buy-side)" : "SSL (sell-side)",
                sweptPool.Price, CurrentBar,
                dir == TradeDirection.Bearish ? "bearish" : "bullish"));

            if (ShowDrawings)
                Draw.ArrowLine(this, "Sweep_" + CurrentBar, false, 0, sweptPool.Price, 0, Close[0],
                    dir == TradeDirection.Bearish ? Brushes.Red : Brushes.Lime);
        }

        private void LookForDisplacementMss()
        {
            // Keep the displacement leg's wick extremes up to date.
            legHigh = Math.Max(legHigh, High[0]);
            legLow = Math.Min(legLow, Low[0]);

            if (CurrentBar - sweepBarIndex > MaxBarsForMss)
            {
                Log("MSS not found within lookback window - invalidating setup.");
                ResetSetup();
                return;
            }

            double body = Math.Abs(Close[0] - Open[0]);
            bool isStrongBody = atr[0] > 0 && body >= DisplacementAtrMultiple * atr[0];

            if (setupDirection == TradeDirection.Bearish)
            {
                // Need a strong bearish candle that closes below the most
                // recent minor swing low formed before the sweep.
                bool bearishCandle = Close[0] < Open[0];
                bool breaksStructure = !double.IsNaN(lastMinorSwingLow) && Close[0] < lastMinorSwingLow;

                if (bearishCandle && isStrongBody && breaksStructure)
                    ConfirmMss();
            }
            else if (setupDirection == TradeDirection.Bullish)
            {
                bool bullishCandle = Close[0] > Open[0];
                bool breaksStructure = !double.IsNaN(lastMinorSwingHigh) && Close[0] > lastMinorSwingHigh;

                if (bullishCandle && isStrongBody && breaksStructure)
                    ConfirmMss();
            }
        }

        private void ConfirmMss()
        {
            mssBarIndex = CurrentBar;
            state = SetupState.AwaitingFvg;

            Log(string.Format("Market Structure Shift confirmed ({0}) on bar {1}. Looking for 3-candle FVG.",
                setupDirection, CurrentBar));

            if (ShowDrawings)
                Draw.Text(this, "MSS_" + CurrentBar, "MSS", 0, setupDirection == TradeDirection.Bearish ? High[0] : Low[0]);
        }

        private void LookForFairValueGap()
        {
            legHigh = Math.Max(legHigh, High[0]);
            legLow = Math.Min(legLow, Low[0]);

            if (CurrentBar - mssBarIndex > MaxBarsForFvg)
            {
                Log("No qualifying FVG formed after MSS - invalidating setup.");
                ResetSetup();
                return;
            }

            // Classic 3-candle FVG: skip the middle (displacement) candle and
            // compare the wicks of the candle two bars back and the current
            // candle. We slide this window forward one bar at a time until a
            // gap appears or the timeout above is hit.
            if (CurrentBar - mssBarIndex < 1)
                return; // need at least one candle after the MSS candle

            if (CurrentBars[0] < 2)
                return;

            double gapTop, gapBottom;

            if (setupDirection == TradeDirection.Bullish)
            {
                // Bullish FVG: Low[0] (most recent candle) > High[2] (candle
                // two bars back), leaving a gap that was never traded.
                gapTop = Low[0];
                gapBottom = High[2];

                if (gapTop <= gapBottom)
                    return; // no gap yet on this window, wait for next bar (or timeout)

                PlaceEntry(gapTop, gapBottom);
            }
            else if (setupDirection == TradeDirection.Bearish)
            {
                // Bearish FVG: High[0] < Low[2].
                gapTop = Low[2];
                gapBottom = High[0];

                if (gapBottom >= gapTop)
                    return;

                PlaceEntry(gapTop, gapBottom);
            }
        }

        private void PlaceEntry(double gapTop, double gapBottom)
        {
            double entryPrice = EntryMode == FvgEntryMode.Midpoint
                ? (gapTop + gapBottom) / 2.0
                : (setupDirection == TradeDirection.Bullish ? gapTop : gapBottom); // proximal boundary

            double tickSize = TickSize;
            double stopBuffer = StopBufferTicks * tickSize;

            double stopPrice = setupDirection == TradeDirection.Bullish
                ? legLow - stopBuffer
                : legHigh + stopBuffer;

            entryPrice = Instrument.MasterInstrument.RoundToTickSize(entryPrice);
            stopPrice = Instrument.MasterInstrument.RoundToTickSize(stopPrice);

            double riskPerUnit = Math.Abs(entryPrice - stopPrice);
            if (riskPerUnit <= 0)
            {
                Log("Degenerate risk distance (entry == stop) - invalidating setup.");
                ResetSetup();
                return;
            }

            // ---- target: fixed R multiple, or the nearer valid opposing pool ----
            double fixedTarget = setupDirection == TradeDirection.Bullish
                ? entryPrice + RewardRiskMultiple * riskPerUnit
                : entryPrice - RewardRiskMultiple * riskPerUnit;

            double targetPrice = fixedTarget;

            if (UseOpposingLiquidityTarget)
            {
                double? opposing = FindOpposingLiquidityTarget(entryPrice, riskPerUnit);
                if (opposing.HasValue)
                    targetPrice = opposing.Value;
            }

            targetPrice = Instrument.MasterInstrument.RoundToTickSize(targetPrice);

            double achievedRR = Math.Abs(targetPrice - entryPrice) / riskPerUnit;
            if (achievedRR < MinRewardRisk)
            {
                Log(string.Format("Achievable R:R {0:F2} below minimum {1:F2} - skipping setup.", achievedRR, MinRewardRisk));
                ResetSetup();
                return;
            }

            // ---- position sizing: risk exactly RiskPercentPerTrade of equity ----
            double equity = GetAccountEquity();
            double riskDollars = equity * (RiskPercentPerTrade / 100.0);
            double dollarRiskPerContract = riskPerUnit * Instrument.MasterInstrument.PointValue;

            if (dollarRiskPerContract <= 0)
            {
                Log("Could not compute dollar risk per contract - invalidating setup.");
                ResetSetup();
                return;
            }

            int quantity = (int)Math.Floor(riskDollars / dollarRiskPerContract);
            quantity = Math.Min(quantity, MaxContracts);

            if (quantity < 1)
            {
                Log(string.Format(
                    "Calculated size < 1 contract (equity {0:C}, risk {1:P1}, stop distance {2}) - skipping setup.",
                    equity, RiskPercentPerTrade / 100.0, riskPerUnit));
                ResetSetup();
                return;
            }

            pendingEntryPrice = entryPrice;
            pendingStopPrice = stopPrice;
            pendingTargetPrice = targetPrice;
            pendingQuantity = quantity;

            activeSignalName = "ICT_" + (setupDirection == TradeDirection.Bullish ? "L" : "S") + "_" + CurrentBar;

            SetStopLoss(activeSignalName, CalculationMode.Price, pendingStopPrice, false);
            SetProfitTarget(activeSignalName, CalculationMode.Price, pendingTargetPrice);

            if (setupDirection == TradeDirection.Bullish)
                entryOrderRef = EnterLongLimit(pendingQuantity, pendingEntryPrice, activeSignalName);
            else
                entryOrderRef = EnterShortLimit(pendingQuantity, pendingEntryPrice, activeSignalName);

            state = SetupState.OrderPending;

            Log(string.Format(
                "{0} FVG limit order placed: qty {1} @ {2} | stop {3} | target {4} | R:R {5:F2}",
                setupDirection, pendingQuantity, pendingEntryPrice, pendingStopPrice, pendingTargetPrice, achievedRR));

            if (ShowDrawings)
            {
                Brush box = setupDirection == TradeDirection.Bullish ? Brushes.LimeGreen : Brushes.OrangeRed;
                Draw.Rectangle(this, "FVG_" + CurrentBar, false, 2, gapTop, 0, gapBottom, box, box, 20);
                Draw.HorizontalLine(this, "Entry_" + CurrentBar, pendingEntryPrice, Brushes.Yellow);
                Draw.HorizontalLine(this, "Stop_" + CurrentBar, pendingStopPrice, Brushes.Red);
                Draw.HorizontalLine(this, "Target_" + CurrentBar, pendingTargetPrice, Brushes.Cyan);
            }
        }

        private double? FindOpposingLiquidityTarget(double entryPrice, double riskPerUnit)
        {
            List<LiquidityPool> opposingPools = setupDirection == TradeDirection.Bullish ? bslPools : sslPools;

            double? best = null;
            double bestDistance = double.MaxValue;

            foreach (LiquidityPool pool in opposingPools)
            {
                if (pool.Swept)
                    continue;

                bool validSide = setupDirection == TradeDirection.Bullish
                    ? pool.Price > entryPrice
                    : pool.Price < entryPrice;

                if (!validSide)
                    continue;

                double rr = Math.Abs(pool.Price - entryPrice) / riskPerUnit;
                if (rr < MinRewardRisk)
                    continue;

                double distance = Math.Abs(pool.Price - entryPrice);
                if (distance < bestDistance)
                {
                    bestDistance = distance;
                    best = pool.Price;
                }
            }

            return best;
        }

        private void ManagePendingOrder()
        {
            if (entryOrderRef == null)
            {
                ResetSetup();
                return;
            }

            if (entryOrderRef.OrderState == OrderState.Filled)
            {
                state = SetupState.InPosition;
                Log("Entry limit order filled - now managing open position via stop/target.");
                return;
            }

            if (entryOrderRef.OrderState == OrderState.Cancelled || entryOrderRef.OrderState == OrderState.Rejected)
            {
                Log("Entry order cancelled/rejected - resetting for next setup.");
                ResetSetup();
                return;
            }

            if (CurrentBar - mssBarIndex > MaxBarsForFill)
            {
                Log("Limit order not filled within timeout - cancelling.");
                CancelOrder(entryOrderRef);
                ResetSetup();
            }
        }

        #endregion

        #region Risk management

        private void ResetDailyState(bool isFirstLoad)
        {
            currentSessionDate = Time[0].Date;
            dayStartEquity = GetAccountEquity();
            dailyLossLockout = false;

            if (!isFirstLoad)
                Log(string.Format("New session ({0:d}) - equity baseline {1:C}, daily loss cap re-armed.",
                    currentSessionDate, dayStartEquity));
        }

        private void CheckDailyLossCap()
        {
            if (dailyLossLockout)
                return;

            double equity = GetAccountEquity();
            double lossPercent = (dayStartEquity - equity) / dayStartEquity * 100.0;

            if (lossPercent >= DailyMaxLossPercent)
            {
                dailyLossLockout = true;
                Log(string.Format(
                    "DAILY MAX LOSS HIT ({0:F2}% >= {1:F2}%) - flattening and blocking new entries for the rest of the session.",
                    lossPercent, DailyMaxLossPercent));
                FlattenAndReset("daily loss cap hit");
            }
        }

        private double GetAccountEquity()
        {
            try
            {
                if (Account != null)
                {
                    double cashValue = Account.Get(AccountItem.CashValue, Currency.UsDollar);
                    if (cashValue > 0)
                        return cashValue;
                }
            }
            catch (Exception ex)
            {
                Log("Account equity lookup failed, using fallback equity: " + ex.Message);
            }

            return FallbackEquity;
        }

        #endregion

        #region Killzone helpers

        private bool IsInKillzone(DateTime easternTime)
        {
            TimeSpan tod = easternTime.TimeOfDay;

            if (UseLondonKillzone && IsWithin(tod, LondonStart, LondonEnd))
                return true;

            if (UseNyAmKillzone && IsWithin(tod, NyAmStart, NyAmEnd))
                return true;

            return false;
        }

        private static bool IsWithin(TimeSpan tod, TimeSpan start, TimeSpan end)
        {
            return tod >= start && tod < end;
        }

        private DateTime ToEasternTime(DateTime barTime)
        {
            TimeZoneInfo sourceZone =
                (Bars != null && Bars.TradingHours != null && Bars.TradingHours.TimeZoneInfo != null)
                    ? Bars.TradingHours.TimeZoneInfo
                    : TimeZoneInfo.Utc;

            DateTime unspecified = DateTime.SpecifyKind(barTime, DateTimeKind.Unspecified);

            try
            {
                DateTime utc = sourceZone.Equals(TimeZoneInfo.Utc)
                    ? DateTime.SpecifyKind(unspecified, DateTimeKind.Utc)
                    : TimeZoneInfo.ConvertTimeToUtc(unspecified, sourceZone);

                return TimeZoneInfo.ConvertTime(utc, EasternZone);
            }
            catch (Exception)
            {
                // If the data series' timezone metadata is unavailable, fall
                // back to treating the bar time as already being Eastern.
                return unspecified;
            }
        }

        #endregion

        #region Flatten / reset helpers

        private void FlattenAndReset(string reason)
        {
            if (Position.MarketPosition != MarketPosition.Flat)
            {
                if (Position.MarketPosition == MarketPosition.Long)
                    ExitLong("Flatten", activeSignalName);
                else
                    ExitShort("Flatten", activeSignalName);

                Log("Flattening open position: " + reason);
            }

            if (entryOrderRef != null &&
                (entryOrderRef.OrderState == OrderState.Working || entryOrderRef.OrderState == OrderState.Accepted))
            {
                CancelOrder(entryOrderRef);
                Log("Cancelling working entry order: " + reason);
            }

            ResetSetup();
        }

        private void ResetSetup()
        {
            state = SetupState.Idle;
            setupDirection = TradeDirection.None;
            sweepBarIndex = -1;
            mssBarIndex = -1;
            legHigh = double.MinValue;
            legLow = double.MaxValue;
            sweptPoolPrice = double.NaN;
            entryOrderRef = null;
            activeSignalName = string.Empty;
        }

        private void Log(string message)
        {
            if (PrintDebug)
                Print(string.Format("{0:HH:mm:ss} [ICT-SB] {1}", Time[0], message));
        }

        #endregion
    }
}
