"""
Markdown digest formatting — fixed template, same spirit as
ict_predictor/report.py's "AUTONOMOUS ICT PREDICTION AGENT REPORT."
"""
from __future__ import annotations

from datetime import datetime, timezone


def _fmt_price(price) -> str:
    if price is None:
        return "—"
    return f"{price:+d}" if isinstance(price, int) else f"{price:+.0f}"


def format_report(cycle: dict) -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# Louisiana Football Quant Agent — Cycle Report",
        f"_Generated {now}_",
        "",
        "**Venue separation:** sports lines from licensed Louisiana sportsbook "
        "consensus (DraftKings/FanDuel/BetMGM via The Odds API, read-only) and "
        "PrizePicks (public projections, read-only). Kalshi is restricted to "
        "CFTC-compliant non-sports macro markets — never matched against "
        "football, and never treated as a sports arbitrage leg.",
        "",
        "**Execution:** signal-only for sportsbooks and PrizePicks — neither "
        "platform offers a vendor API for placing wagers on a personal account, "
        "and automated submission would violate both platforms' terms of "
        "service, so this agent never attempts it. Kalshi order submission is "
        "dry-run unless LQ_KALSHI_LIVE=1 is explicitly set.",
        "",
    ]

    ev_hits = cycle.get("ev_bets", [])
    lines.append(f"## +EV Sportsbook Bets ({len(ev_hits)})")
    if ev_hits:
        lines.append("| League | Game | Market | Side | Book | Price | Fair % | Edge % |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for h in ev_hits[:25]:
            point = h.get("point")
            side_label = f"{h['side']} {point}" if point is not None else h["side"]
            lines.append(
                f"| {h['league'].upper()} | {h['game']} | {h['market']} | "
                f"{side_label} | "
                f"{h['book']} | {_fmt_price(h['price'])} | {h['fair_prob_pct']}% | "
                f"**{h['edge_pct']}%** |"
            )
    else:
        lines.append("_None above the LQ_MIN_EV_PCT threshold this cycle._")
    lines.append("")

    arb_hits = cycle.get("arbitrage", [])
    lines.append(f"## Cross-Book Arbitrage ({len(arb_hits)})")
    if arb_hits:
        lines.append("| League | Game | Market | Legs | Guaranteed Profit % |")
        lines.append("|---|---|---|---|---|")
        for h in arb_hits[:25]:
            legs = "; ".join(
                f"{leg['side']} @ {leg['book']} ({_fmt_price(leg['price'])}, "
                f"stake {leg['stake_pct']}%)" for leg in h["legs"]
            )
            lines.append(
                f"| {h['league'].upper()} | {h['game']} | {h['market']} | "
                f"{legs} | **{h['guaranteed_profit_pct']}%** |"
            )
    else:
        lines.append("_None found this cycle — true two-way arbs are rare and "
                      "close within minutes._")
    lines.append("")

    pp_hits = cycle.get("prizepicks_value", [])
    lines.append(f"## PrizePicks Value Scan — heuristic ({len(pp_hits)})")
    if pp_hits:
        lines.append("| League | Player | Stat | Line | Type | Note |")
        lines.append("|---|---|---|---|---|---|")
        for h in pp_hits[:25]:
            lines.append(
                f"| {h['league']} | {h['player']} | {h['stat_type']} | {h['line']} | "
                f"{h['odds_type']} | {h['note']} |"
            )
    else:
        lines.append("_No demon/goblin lines flagged this cycle._")
    lines.append("")

    kalshi = cycle.get("kalshi_markets", [])
    lines.append(f"## Kalshi Macro Sleeve ({len(kalshi)} open markets, "
                  "non-sports only)")
    if kalshi:
        lines.append("| Ticker | Category | Title | Yes Bid/Ask | 24h Vol |")
        lines.append("|---|---|---|---|---|")
        for m in kalshi[:20]:
            lines.append(
                f"| {m['ticker']} | {m['category']} | {m['title'][:60]} | "
                f"{m.get('yes_bid', '—')}¢ / {m.get('yes_ask', '—')}¢ | "
                f"{m.get('volume_24h', 0)} |"
            )
    else:
        lines.append("_No macro markets fetched this cycle "
                      f"({cycle.get('kalshi_error', 'no data')})._")
    lines.append("")

    lines.append("---")
    lines.append(
        f"_Kalshi live trading: {'**ARMED**' if cycle.get('kalshi_live') else 'dry-run'}. "
        "Sportsbook/PrizePicks: always signal-only. Not financial advice; "
        "sports wagering carries substantial risk of loss and is subject to "
        "Louisiana gaming law and each platform's terms of service._"
    )
    return "\n".join(lines)
