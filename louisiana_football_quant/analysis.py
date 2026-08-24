"""
Pure price-math analysis — no LLM, no invented numbers, same philosophy as
ict_predictor's Decision & Validation Matrix ("never LLM-invented levels").

Three independent scans:
  1. +EV bets      — a book's price is better than the no-vig consensus
                      fair price by at least LQ_MIN_EV_PCT.
  2. Two-way arbs   — opposite sides of the same market, at different books,
                      guarantee a profit regardless of outcome.
  3. PrizePicks value — heuristic read of demon/goblin payout multipliers
                      against a 50/50 baseline. This is NOT a matched-market
                      arbitrage (see prizepicks_client.py docstring) — it is
                      flagged as a heuristic in every output, never as a
                      guaranteed edge.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Odds math
# ---------------------------------------------------------------------------

def american_to_prob(odds: float) -> float:
    """Implied probability from an American odds price (vig included)."""
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return -odds / (-odds + 100.0)


def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return 1 + odds / 100.0
    return 1 + 100.0 / (-odds)


def devig_multiplicative(implied_probs: list[float]) -> list[float]:
    """Remove vig proportionally across a set of mutually-exclusive outcome
    prices (the standard 'multiplicative'/proportional devig). Returns fair
    probabilities that sum to 1.0."""
    total = sum(implied_probs)
    if total <= 0:
        return implied_probs
    return [p / total for p in implied_probs]


# ---------------------------------------------------------------------------
# +EV scan
# ---------------------------------------------------------------------------

def min_ev_pct() -> float:
    return float(os.environ.get("LQ_MIN_EV_PCT", "2.0"))


def _outcomes_by_market(event: dict) -> dict:
    """bookmaker odds → {(market_key, outcome_name, point): [{book, price}]}"""
    grouped: dict = {}
    for book in event.get("bookmakers", []):
        book_key = book.get("key", book.get("title", "?"))
        for market in book.get("markets", []):
            mkey = market.get("key", "")
            for outcome in market.get("outcomes", []):
                point = outcome.get("point")
                gkey = (mkey, outcome.get("name", ""), point)
                grouped.setdefault(gkey, []).append({
                    "book": book_key,
                    "price": outcome.get("price"),
                })
    return grouped


def find_ev_bets(events: list[dict], league: str) -> list[dict]:
    """For each market, build a no-vig consensus from the best price per book
    on each side, then flag any single book whose price beats that fair price
    by >= LQ_MIN_EV_PCT."""
    threshold = min_ev_pct()
    hits: list[dict] = []

    for event in events:
        grouped = _outcomes_by_market(event)
        # Pair up the two (or three, for 3-way soccer-style — not used here)
        # sides of each market so we can devig them together.
        market_sides: dict = {}
        for (mkey, name, point), quotes in grouped.items():
            market_sides.setdefault((mkey, point), {})[name] = quotes

        for (mkey, point), sides in market_sides.items():
            if len(sides) < 2:
                continue
            # Best (highest) price offered per side, used only to build the
            # no-vig baseline — not necessarily the book we flag below.
            best_per_side = {name: max(q["price"] for q in quotes) for name, quotes in sides.items()}
            implied = [american_to_prob(p) for p in best_per_side.values()]
            fair = devig_multiplicative(implied)
            fair_by_side = dict(zip(best_per_side.keys(), fair))

            for name, quotes in sides.items():
                fair_prob = fair_by_side.get(name)
                if not fair_prob:
                    continue
                fair_decimal = 1 / fair_prob if fair_prob > 0 else None
                for q in quotes:
                    price, book = q["price"], q["book"]
                    if price is None:
                        continue
                    offered_decimal = american_to_decimal(price)
                    if not fair_decimal:
                        continue
                    edge_pct = (offered_decimal / fair_decimal - 1) * 100
                    if edge_pct >= threshold:
                        hits.append({
                            "league": league,
                            "game": f"{event.get('away_team', '?')} @ {event.get('home_team', '?')}",
                            "commence_time": event.get("commence_time", ""),
                            "market": mkey,
                            "side": name,
                            "point": point,
                            "book": book,
                            "price": price,
                            "fair_prob_pct": round(fair_prob * 100, 2),
                            "edge_pct": round(edge_pct, 2),
                        })
    hits.sort(key=lambda h: -h["edge_pct"])
    return hits


# ---------------------------------------------------------------------------
# Two-way arbitrage scan
# ---------------------------------------------------------------------------

def min_arb_pct() -> float:
    return float(os.environ.get("LQ_MIN_ARB_PCT", "0.5"))


def find_arbitrage(events: list[dict], league: str) -> list[dict]:
    """Best price per side, from possibly different books, on a two-outcome
    market. If the sum of implied probabilities (using each side's BEST
    price) is < 1.0, staking proportionally locks in a profit regardless of
    the outcome."""
    threshold = min_arb_pct()
    hits: list[dict] = []

    for event in events:
        grouped = _outcomes_by_market(event)
        market_sides: dict = {}
        for (mkey, name, point), quotes in grouped.items():
            market_sides.setdefault((mkey, point), {})[name] = quotes

        for (mkey, point), sides in market_sides.items():
            if len(sides) != 2:
                continue  # arb math here is for true two-way markets only
            best = {}
            for name, quotes in sides.items():
                top = max(quotes, key=lambda q: q["price"])
                best[name] = top

            implied_sum = sum(american_to_prob(v["price"]) for v in best.values())
            if implied_sum >= 1.0:
                continue
            profit_pct = (1.0 / implied_sum - 1.0) * 100
            if profit_pct < threshold:
                continue

            hits.append({
                "league": league,
                "game": f"{event.get('away_team', '?')} @ {event.get('home_team', '?')}",
                "commence_time": event.get("commence_time", ""),
                "market": mkey,
                "point": point,
                "legs": [
                    {"side": name, "book": v["book"], "price": v["price"],
                     "stake_pct": round(american_to_prob(v["price"]) / implied_sum * 100, 2)}
                    for name, v in best.items()
                ],
                "guaranteed_profit_pct": round(profit_pct, 2),
            })
    hits.sort(key=lambda h: -h["guaranteed_profit_pct"])
    return hits


# ---------------------------------------------------------------------------
# PrizePicks heuristic value scan
# ---------------------------------------------------------------------------

def prizepicks_value_scan(projections: list[dict]) -> list[dict]:
    """Flag demon/goblin lines whose implied breakeven probability looks far
    from the 50/50 a straight PrizePicks line targets. This is a heuristic
    read of the payout structure, NOT a matched sportsbook comparison — every
    hit is labeled 'heuristic' so report.py never presents it as a hard edge.
    """
    threshold = float(os.environ.get("LQ_PRIZEPICKS_HEURISTIC_PCT", "10.0"))
    hits = []
    for proj in projections:
        odds_type = (proj.get("odds_type") or "standard").lower()
        if odds_type == "standard":
            continue  # standard lines are PrizePicks' own 50/50 target, no signal
        # Demon lines pay a boosted multiplier for a harder-to-hit "over" —
        # goblin lines pay a reduced multiplier for an easier "under." Absent
        # a matched sportsbook prop line, the multiplier's implied deviation
        # from 50% is the only signal available; treat it as directional only.
        deviation_pct = 15.0 if odds_type == "demon" else 10.0
        if deviation_pct < threshold:
            continue
        hits.append({
            "player": proj.get("player", "?"),
            "team": proj.get("team", ""),
            "league": proj.get("league", ""),
            "stat_type": proj.get("stat_type", ""),
            "line": proj.get("line"),
            "odds_type": odds_type,
            "note": "heuristic — payout-implied deviation from 50/50, no matched sportsbook line",
        })
    return hits
