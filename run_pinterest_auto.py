#!/usr/bin/env python3
"""
Pinterest Auto-Poster — publishes pins on a daily schedule.

Pin types it rotates through:
  - SELLER pins:    "Sell your house fast in [City]" → seller landing page
  - BUYER pins:     "Cash buyers: priority deal access" → wholesaleomniverse.com
  - WHOLESALER pins:"Wholesaling 101" → deal analyzer trial
  - AFFILIATE pins: "Best tools for wholesalers" → affiliate URLs from .env

Usage:
  python3 run_pinterest_auto.py                # publishes today's pin set
  python3 run_pinterest_auto.py --dry-run      # show what would post
  python3 run_pinterest_auto.py --type seller --city "Detroit, MI"
  python3 run_pinterest_auto.py --status       # show creds + recent pins
"""
import argparse
import datetime
import json
import os
import random
import sys
import time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
import requests

console = Console()
API = "https://api.pinterest.com/v5"

DATA_DIR     = Path(__file__).parent / "data"
PIN_LOG      = DATA_DIR / "pinterest_log.json"
DEFAULT_IMG  = "https://files.catbox.moe/u534iv.png"  # the Wholesale Omniverse logo

# Where pins link to — env-overridable for landing pages later
LANDING_SELLER   = os.environ.get("PINTEREST_LANDING_SELLER",   "https://wholesaleomniverse.com/sell")
LANDING_BUYER    = os.environ.get("PINTEREST_LANDING_BUYER",    "https://wholesaleomniverse.com/buyers")
LANDING_DEALER   = os.environ.get("PINTEREST_LANDING_WHOLESALE","https://wholesaleomniverse.com/deal-analyzer")

# Affiliate URLs — populate these in .env once you've signed up
AFFILIATE_PROGRAMS = {
    "batchskiptracing": os.environ.get("AFFILIATE_BATCHSKIPTRACING_URL", ""),
    "carrot":           os.environ.get("AFFILIATE_CARROT_URL", ""),
    "propstream":       os.environ.get("AFFILIATE_PROPSTREAM_URL", ""),
    "reisift":          os.environ.get("AFFILIATE_REISIFT_URL", ""),
}

# Markets to rotate seller pins through
DEFAULT_MARKETS = [
    "Detroit, MI", "Memphis, TN", "Atlanta, GA", "Cleveland, OH",
    "Chicago, IL", "Birmingham, AL", "Jacksonville, FL", "Tampa, FL",
    "Charlotte, NC", "Nashville, TN", "Kansas City, MO", "Indianapolis, IN",
    "Baltimore, MD", "Philadelphia, PA", "New Orleans, LA",
]


def _log(entry: dict):
    log = json.loads(PIN_LOG.read_text()) if PIN_LOG.exists() else []
    log.append({**entry, "logged_at": datetime.datetime.now().isoformat()})
    PIN_LOG.write_text(json.dumps(log, indent=2))


# ── Content generators ──────────────────────────────────────────────────────
def seller_pin(city: str = "") -> dict:
    if not city:
        city = random.choice(DEFAULT_MARKETS)
    city_name = city.split(",")[0]
    return {
        "type": "seller",
        "title": f"List Your {city_name} Home to 100+ Cash Buyers — Free",
        "description": (
            f"Free service for homeowners in {city_name}: submit your property and we put it on "
            f"our weekly buyers list seen by 100+ active cash investors. They contact you directly "
            f"with offers. No agent commissions, no MLS, no fees ever. Distressed, inherited, "
            f"pre-foreclosure — all welcome. "
            f"#SellMyHouseFast #CashHomeBuyers #{city_name.replace(' ', '')} #NoAgent #RealEstate"
        )[:480],
        "link": LANDING_SELLER + f"?city={city_name.replace(' ', '-').lower()}",
        "image_url": DEFAULT_IMG,
    }


def buyer_pin() -> dict:
    return {
        "type": "buyer",
        "title": "Weekly Off-Market Property Lists for Real Estate Investors",
        "description": (
            "Stop hunting for deals — get a curated motivated-seller property list "
            "delivered weekly. Distressed, pre-foreclosure, probate, tax delinquent, "
            "and absentee-owner properties in Detroit, Memphis, Atlanta, Cleveland, "
            "Chicago + 10 other markets. Each entry: address, owner contact, ARV, "
            "and repair estimate. You make offers directly. $97/month. Cancel anytime. "
            "#CashBuyers #OffMarket #RealEstateInvesting #FixAndFlip #BRRRR "
            "#MotivatedSellers #WholesaleDeals"
        )[:480],
        "link": LANDING_BUYER,
        "image_url": DEFAULT_IMG,
    }


def wholesaler_pin() -> dict:
    return {
        "type": "wholesaler",
        "title": "AI Deal Analyzer — ARV, Max Offer, LOI in 60 Seconds",
        "description": (
            "Stop analyzing deals manually. Our AI pulls comps, estimates ARV, calculates max offer, "
            "and drafts your LOI in under a minute. Built by wholesalers for wholesalers. "
            "$197/month, free 7-day trial. "
            "#Wholesaling #RealEstateInvesting #DealAnalysis #PropTech #BiggerPockets"
        )[:480],
        "link": LANDING_DEALER,
        "image_url": DEFAULT_IMG,
    }


def affiliate_pin() -> dict:
    """Pick a random affiliate program with a URL set + craft a content pin."""
    live = [(k, v) for k, v in AFFILIATE_PROGRAMS.items() if v]
    if not live:
        return {}  # no affiliate URLs configured yet
    program, url = random.choice(live)
    templates = {
        "batchskiptracing": (
            "Skip Tracing Tool That Actually Returns Real Phone Numbers",
            "BatchSkipTracing is what I use to skip-trace motivated seller lists. "
            "$0.13/record, fast turnaround, way better hit rate than the free tools. "
            "Try it free → "
        ),
        "carrot": (
            "The Landing Page Service Most Wholesalers Use (Carrot)",
            "Carrot landing pages convert motivated sellers way better than DIY sites. "
            "Built-in SEO templates, lead capture forms, and CRM. "
            "Free trial → "
        ),
        "propstream": (
            "How I Find Off-Market Property Lists in Minutes (PropStream)",
            "PropStream pulls pre-foreclosure, probate, tax delinquent, and absentee owner lists "
            "for any market. The fastest way to build a motivated seller list. "
            "Free trial → "
        ),
        "reisift": (
            "The CRM Built for Real Estate Wholesalers (REISift)",
            "REISift cleans, dedupes, and skip-traces your lists, then sequences your follow-ups. "
            "If you're scaling past 1,000 leads, this is the tool. "
            "Try it → "
        ),
    }
    title, desc = templates.get(program, (f"Tool I recommend: {program}", "Try it → "))
    return {
        "type": f"affiliate:{program}",
        "title": title[:100],
        "description": (desc + url + " #Wholesaling #RealEstateInvesting #RealEstateTools")[:480],
        "link": url,
        "image_url": DEFAULT_IMG,
    }


# ── Pinterest API ────────────────────────────────────────────────────────────
def post_pin(pin: dict, dry_run: bool = False) -> dict:
    if dry_run:
        return {"status": "dry_run", "would_post": pin}

    token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
    board = os.environ.get("PINTEREST_BOARD_ID", "")
    if not token or not board:
        return {"status": "skipped",
                "reason": "missing PINTEREST_ACCESS_TOKEN or PINTEREST_BOARD_ID — "
                          "run python3 setup_pinterest.py"}

    payload = {
        "board_id":      board,
        "title":         pin["title"],
        "description":   pin["description"],
        "link":          pin["link"],
        "media_source":  {"source_type": "image_url", "url": pin["image_url"]},
    }
    try:
        r = requests.post(f"{API}/pins",
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json"},
                          json=payload, timeout=20)
        if r.status_code in (200, 201):
            data = r.json()
            _log({"pin_id": data.get("id"), "type": pin["type"],
                  "title": pin["title"], "link": pin["link"],
                  "status": "posted"})
            return {"status": "posted", "pin_id": data.get("id"),
                    "url": f"https://www.pinterest.com/pin/{data.get('id')}/"}
        return {"status": "failed",
                "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        return {"status": "failed", "error": str(e)}


# ── Daily rotation ───────────────────────────────────────────────────────────
def build_daily_set(forced_type: str = "", forced_city: str = "") -> list:
    """3 pins/day default rotation: 1 seller + 1 buyer-or-wholesaler + 1 affiliate."""
    if forced_type:
        builder = {
            "seller":     lambda: seller_pin(forced_city),
            "buyer":      buyer_pin,
            "wholesaler": wholesaler_pin,
            "affiliate":  affiliate_pin,
        }.get(forced_type)
        if not builder:
            return []
        pin = builder()
        return [pin] if pin else []

    pins = [seller_pin(forced_city)]
    pins.append(buyer_pin() if datetime.datetime.now().weekday() % 2 == 0 else wholesaler_pin())
    aff = affiliate_pin()
    if aff:
        pins.append(aff)
    return pins


# ── CLI ──────────────────────────────────────────────────────────────────────
def cmd_status():
    token = os.environ.get("PINTEREST_ACCESS_TOKEN", "")
    board = os.environ.get("PINTEREST_BOARD_ID", "")
    aff_live = [k for k, v in AFFILIATE_PROGRAMS.items() if v]

    tbl = Table(title="Pinterest Status", border_style="blue")
    tbl.add_column("Field"); tbl.add_column("Value")
    tbl.add_row("PINTEREST_ACCESS_TOKEN", f"[{'green' if token else 'red'}]{'set' if token else 'MISSING'}[/]")
    tbl.add_row("PINTEREST_BOARD_ID",     f"[{'green' if board else 'red'}]{board or 'MISSING'}[/]")
    tbl.add_row("Affiliate programs",     ", ".join(aff_live) if aff_live else "[dim]none configured[/dim]")
    console.print(tbl)

    if PIN_LOG.exists():
        log = json.loads(PIN_LOG.read_text())
        console.print(f"\n  Pins posted (lifetime): [bold]{len(log)}[/bold]")
        if log:
            console.print(f"  Last pin: {log[-1].get('title', '')[:60]}  "
                          f"({log[-1].get('logged_at', '')[:10]})")


def cmd_run(dry_run: bool, forced_type: str, forced_city: str):
    pins = build_daily_set(forced_type, forced_city)
    if not pins:
        console.print("[red]No pins to publish (check --type value).[/red]"); return

    console.print(Panel(
        Text.from_markup(
            f"[bold]Pinterest Daily Set[/bold]\n"
            f"  Pins this run: [white]{len(pins)}[/white]\n"
            f"  Mode: {'[yellow]DRY RUN[/yellow]' if dry_run else '[red]LIVE[/red]'}"
        ),
        border_style="blue",
        title="[bold blue]Wholesale Omniverse — Pinterest[/bold blue]",
    ))

    for i, pin in enumerate(pins, 1):
        console.print(f"\n[cyan]──── Pin {i}/{len(pins)}  ({pin['type']}) ────[/cyan]")
        console.print(f"  [bold]{pin['title']}[/bold]")
        console.print(f"  Link:        {pin['link']}")
        console.print(f"  Description: {pin['description'][:200]}{'...' if len(pin['description'])>200 else ''}")
        result = post_pin(pin, dry_run=dry_run)
        status = result.get("status", "?")
        if status == "posted":
            console.print(f"  [green]✓ Posted:[/green] {result.get('url')}")
        elif status == "dry_run":
            console.print(f"  [cyan]✓ Dry run[/cyan]")
        elif status == "skipped":
            console.print(f"  [yellow]⚠ {result.get('reason')}[/yellow]")
        else:
            console.print(f"  [red]✗ {result.get('error', '')[:200]}[/red]")
        time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description="Pinterest Auto-Poster")
    parser.add_argument("--dry-run", action="store_true", help="Preview without posting")
    parser.add_argument("--type", default="", choices=["", "seller", "buyer", "wholesaler", "affiliate"],
                        help="Force a single pin of this type instead of the daily set")
    parser.add_argument("--city", default="", help="Force a specific market for seller pins")
    parser.add_argument("--status", action="store_true", help="Show credentials + pin history")
    args = parser.parse_args()

    if args.status:
        cmd_status(); return
    cmd_run(args.dry_run, args.type, args.city)


if __name__ == "__main__":
    main()
