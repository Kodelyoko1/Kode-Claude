"""
Per-agent subscription paywall.
Each agent has its own Stripe payment link. Clients pay and get an access key.
Owner bypasses paywall with AGENT_PASSWORD from .env.
"""
import json
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
SUBS_FILE = DATA_DIR / "agent_subscriptions.json"

AGENT_NAMES = {
    "buyer_finder":     "Cash Buyer Finder Agent",
    "followup":         "Seller Follow-Up Agent",
    "outreach":         "Outreach-as-a-Service Agent",
    "wholesale":        "Wholesale Deal Analyzer",
    # — autonomous revenue agents —
    "reputation_guard": "ReputationGuard — Review Management",
    "towncrier":        "TownCrier — Local Newsletter Sponsorships",
    "gutenberg_voice":  "GutenbergVoice — Audiobook Script Marketplace",
    "trendscout":       "TrendScout — Trend Intelligence Newsletter",
    "link_mender":      "LinkMender — Dead Link SEO Audits",
    "careerforge":      "CareerForge — Resume Tailoring",
    "paperbrief":       "PaperBrief — Research Newsletter",
    "nichelens":        "NicheLens — Niche Curation",
    "storyforge":       "StoryForge — Writers' Coaching",
    "pantrychef":       "PantryChef — Meal Plan Subscription",
    "shortsforge":      "ShortsForge — YouTube Shorts Architect",
    "viral_recycler":   "ViralRecycler — YouTube + TikTok Auto-Poster",
    "transcribe":       "Transcribe — Audio/Video Transcription",
    "shownotes":        "ShowNotes — Podcast Show Notes Writer",
    "thumbforge":       "ThumbForge — YouTube/Shorts Thumbnail Designer",
    "carouselforge":    "CarouselForge — LinkedIn/IG Carousel Designer",
    "seowriter":        "SEOWriter — SEO Article Drafting",
    "speedaudit":       "SpeedAudit — Website Performance Audits",
    "inboxzero":        "InboxZero — Autonomous Inbox Triage",
    "courseforge":      "CourseForge — Mini-Course Packager",
    "localize":         "Localize — Translation + Localization",
    "notiontemplate":   "NotionTemplate — Productized Notion Templates",
    "podcleaner":       "PodCleaner — Podcast Audio Cleanup",
    "proofbot":         "ProofBot — Proofreader + Copyeditor",
    "modbot":           "ModBot — Social Comment Moderation",
    "chatconfig":       "ChatConfig — Importable FAQ Chatbot Bundles",
    "bentoforge":       "BentoForge — Link-in-Bio Landing Pages",
    "templateforge":    "TemplateForge — Design Templates + Briefs",
    "plannerforge":     "PlannerForge — PDF Planner Generator",
    "deckforge":        "DeckForge — Pitch Deck Generator",
    "domainscout":      "DomainScout — Domain Candidate Lists",
    "propscout":        "PropScout — Free PropStream-Style Prospect Engine",
    "coldcaller":       "ColdCaller — Google Voice Click-to-Call Queue",
    "salespage_doctor": "SalesPageDoctor — Creator Sales-Page Audits",
    "media_buyer":      "MediaBuyer — Autonomous Meta-Ads Optimizer",
    "hudscout":         "HUDScout — HUD-Owned Foreclosed Property Feed",
    "dropship_scout":   "DropshipScout — TikTok-Shop + Amazon Trend Digest",
}

# Default prices — overridden by .env PAYWALL_<AGENT>_PRICE vars
DEFAULT_PRICES = {
    "buyer_finder":     97,
    "followup":         147,
    "outreach":         297,
    "wholesale":        197,
    "reputation_guard": 79,
    "towncrier":        100,
    "gutenberg_voice":  29,
    "trendscout":       29,
    "link_mender":      47,
    "careerforge":      29,
    "paperbrief":       39,
    "nichelens":        7,
    "storyforge":       19,
    "pantrychef":       14,
    "shortsforge":      5,
    "viral_recycler":   29,
    "transcribe":       19,
    "shownotes":        29,
    "thumbforge":       49,
    "carouselforge":    99,
    "seowriter":        149,
    "speedaudit":       37,
    "inboxzero":        97,
    "courseforge":      99,
    "localize":         49,
    "notiontemplate":   49,
    "podcleaner":       49,
    "proofbot":         39,
    "modbot":           97,
    "chatconfig":       49,
    "bentoforge":       19,
    "templateforge":    49,
    "plannerforge":     29,
    "deckforge":        49,
    "domainscout":      79,
    "propscout":        97,
    "coldcaller":       97,
    "salespage_doctor": 77,
    "media_buyer":      297,
    "hudscout":         97,
    "dropship_scout":   47,
}


def _price(agent_key: str) -> float:
    env_key = f"PAYWALL_{agent_key.upper()}_PRICE"
    try:
        return float(os.environ.get(env_key, DEFAULT_PRICES.get(agent_key, 97)))
    except (ValueError, TypeError):
        return float(DEFAULT_PRICES.get(agent_key, 97))


def _load() -> dict:
    if SUBS_FILE.exists():
        with open(SUBS_FILE) as f:
            return json.load(f)
    return {}


def _save(data: dict):
    DATA_DIR.mkdir(exist_ok=True)
    with open(SUBS_FILE, "w") as f:
        json.dump(data, f, indent=2)


def is_owner() -> bool:
    """True when the AGENT_PASSWORD env var is set (owner running their own instance)."""
    return bool(os.environ.get("AGENT_PASSWORD"))


def check_access(agent_key: str, access_key: str = "") -> dict:
    """
    Verify a client has paid for this agent.
    Returns {"allowed": True} or {"allowed": False, "message": "...", "payment_url": "..."}
    """
    if is_owner():
        return {"allowed": True, "reason": "owner"}

    subs = _load()
    sub = subs.get(access_key)

    if not sub:
        return {
            "allowed": False,
            "message": "Access key not found. Subscribe to get access.",
            "payment_url": "",
        }

    if sub.get("agent") != agent_key:
        return {
            "allowed": False,
            "message": f"This key is for {sub.get('agent')}, not {agent_key}.",
            "payment_url": sub.get("payment_url", ""),
        }

    if sub.get("status") != "active":
        return {
            "allowed": False,
            "message": "Payment pending. Pay via the link below to get access.",
            "payment_url": sub.get("payment_url", ""),
            "amount_due": sub.get("price"),
        }

    # Check expiry
    expires = sub.get("expires_at", "")
    if expires and datetime.fromisoformat(expires) < datetime.now():
        subs[access_key]["status"] = "expired"
        _save(subs)
        return {
            "allowed": False,
            "message": "Subscription expired. Renew to continue.",
            "payment_url": sub.get("payment_url", ""),
        }

    return {"allowed": True, "reason": "active_subscription", "name": sub.get("name")}


def _activate_sub(access_key: str):
    subs = _load()
    if access_key in subs:
        subs[access_key]["status"] = "active"
        subs[access_key]["activated_at"] = datetime.now().isoformat()
        subs[access_key]["expires_at"] = (datetime.now() + timedelta(days=30)).isoformat()
        _save(subs)


def _stripe_link(agent_key: str) -> str:
    """Return the Stripe payment link for this agent, or PayPal.me fallback."""
    env_key = f"STRIPE_LINK_{agent_key.upper()}"
    link = os.environ.get(env_key, "").strip()
    if link:
        return link
    # PayPal.me fallback (no API needed)
    username = os.environ.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")
    price = _price(agent_key)
    return f"https://paypal.me/{username}/{price:.0f}"


def create_subscription(
    agent_key: str,
    client_name: str,
    client_email: str,
) -> dict:
    """
    Register a new client subscription and return a Stripe payment link.
    The client pays via Stripe; owner activates their key after payment clears.
    """
    access_key = f"WO-{agent_key.upper()[:3]}-{uuid.uuid4().hex[:8].upper()}"
    price = _price(agent_key)
    payment_url = _stripe_link(agent_key)
    method = "stripe" if "stripe.com" in payment_url else "paypal_me"

    record = {
        "access_key":  access_key,
        "agent":       agent_key,
        "name":        client_name,
        "email":       client_email,
        "price":       price,
        "status":      "pending_payment",
        "method":      method,
        "payment_url": payment_url,
        "created_at":  datetime.now().isoformat(),
        "expires_at":  "",
    }

    subs = _load()
    subs[access_key] = record
    _save(subs)

    return {
        "access_key":  access_key,
        "payment_url": payment_url,
        "price":       price,
        "method":      method,
        "message": (
            f"Share the payment link with {client_name}.\n"
            f"Their access key is: {access_key}\n"
            f"Once they pay, run: python3 manage_clients.py --activate {access_key}"
        ),
    }


def activate_subscription(access_key: str) -> dict:
    """Manually activate a subscription (for cash/Zelle/Venmo payments)."""
    subs = _load()
    if access_key not in subs:
        return {"error": f"Access key {access_key} not found."}
    _activate_sub(access_key)
    sub = subs[access_key]
    return {
        "status": "activated",
        "access_key": access_key,
        "name": sub.get("name"),
        "agent": sub.get("agent"),
        "expires_at": (datetime.now() + timedelta(days=30)).isoformat(),
    }


def list_subscriptions(agent_key: str = "") -> dict:
    """List all subscriptions, optionally filtered by agent."""
    subs = _load()
    items = list(subs.values())
    if agent_key:
        items = [s for s in items if s.get("agent") == agent_key]
    active  = [s for s in items if s.get("status") == "active"]
    pending = [s for s in items if s.get("status") == "pending_payment"]
    mrr = sum(s.get("price", 0) for s in active)
    return {
        "total": len(items),
        "active": len(active),
        "pending_payment": len(pending),
        "mrr": mrr,
        "subscriptions": items,
    }


def paywall_prompt(agent_key: str) -> bool:
    """
    Interactive paywall prompt for CLI scripts.
    Returns True if access is granted, False to exit.
    Owner (AGENT_PASSWORD set) always passes.
    """
    from rich.console import Console
    from rich.panel import Panel
    from rich.text import Text
    console = Console()

    if is_owner():
        return True

    price = _price(agent_key)
    agent_label = AGENT_NAMES.get(agent_key, agent_key)
    username = os.environ.get("PAYPAL_ME_USERNAME", "wholesaleomniverse")

    console.print(Panel(
        Text.from_markup(
            f"[bold yellow]Access Required[/bold yellow]\n\n"
            f"  [white]{agent_label}[/white] — ${price:.0f}/month\n\n"
            f"  [bold]Already subscribed?[/bold] Enter your access key below.\n"
            f"  [bold]New subscriber?[/bold]  Enter your name + email to generate payment link.\n\n"
            f"  [dim]Or pay directly: paypal.me/{username}/{price:.0f}[/dim]"
        ),
        title="[bold blue]Wholesale Omniverse — Paywall[/bold blue]",
        border_style="yellow",
    ))

    choice = input("  Do you have an access key? (y/n): ").strip().lower()

    if choice == "y":
        key = input("  Enter your access key: ").strip()
        result = check_access(agent_key, key)
        if result.get("allowed"):
            console.print(f"[green]✓ Access granted. Welcome{', ' + result['name'] if result.get('name') else ''}![/green]\n")
            return True
        else:
            console.print(f"[red]✗ {result.get('message')}[/red]")
            if result.get("payment_url"):
                console.print(f"  Payment link: {result['payment_url']}")
            return False
    else:
        name  = input("  Your full name: ").strip()
        email = input("  Your email: ").strip()
        if not name or not email:
            console.print("[red]Name and email required.[/red]")
            return False
        result = create_subscription(agent_key, name, email)
        console.print(Panel(
            Text.from_markup(
                f"[bold green]Payment Link Created[/bold green]\n\n"
                f"  Pay here: [bold]{result['payment_url']}[/bold]\n\n"
                f"  Your access key: [bold yellow]{result['access_key']}[/bold yellow]\n"
                f"  Save this key — you'll need it to log in after payment.\n\n"
                f"  Once payment clears, re-run this script and enter your key."
            ),
            border_style="green",
        ))
        return False
