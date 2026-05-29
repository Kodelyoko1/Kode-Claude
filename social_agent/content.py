"""
Content generator for social posts.
Rotates copy across three audiences: motivated sellers, cash buyers, wholesalers.
"""
import random
import datetime
from pathlib import Path

PAYPAL_ME = "paypal.me/wholesaleomniverse"
COMPANY_URL = "https://wholesaleomniverse.com"

# Each entry: title, body, audience, cta, hashtags
SELLER_POSTS = [
    {
        "title": "Skip the agent. List your house to 100+ cash buyers — free.",
        "body": (
            "Free service for homeowners: submit your property and we put it in front of "
            "100+ active cash investors in your market. They contact you directly with offers. "
            "No agent fees, no MLS, no public listing. Distressed, inherited, behind on payments — "
            "all welcome."
        ),
        "audience": "sellers",
        "cta": "DM the address — we list you on Monday's drop.",
        "hashtags": ["#CashHomeBuyers", "#SellMyHouseFast", "#RealEstate", "#NoAgent"],
    },
    {
        "title": "Behind on the mortgage? List free, talk to cash buyers directly.",
        "body": (
            "Pre-foreclosure properties get the most attention on our weekly buyers list. "
            "100+ active cash investors compete for distressed homes in Detroit, Memphis, Atlanta, "
            "Chicago, and Cleveland. You get offers in days, not months. We don't charge sellers — ever."
        ),
        "audience": "sellers",
        "cta": "DM your situation and we'll list your property free.",
        "hashtags": ["#PreForeclosure", "#SellMyHouseFast", "#CashOffer", "#RealEstate"],
    },
    {
        "title": "Inherited a house you don't want? List it free.",
        "body": (
            "Probate properties are some of the most-requested on our weekly cash-buyer list. "
            "Submit yours and 100+ investors will see it next Monday — they'll call you directly "
            "with offers. Sellers list free; we make our money from buyer subscriptions."
        ),
        "audience": "sellers",
        "cta": "DM the property address to get listed.",
        "hashtags": ["#ProbateProperty", "#InheritedHouse", "#SellMyHouseFast", "#CashBuyers"],
    },
]

BUYER_POSTS = [
    {
        "title": "Weekly motivated-seller property lists for cash buyers — $97/mo",
        "body": (
            "Every week we deliver a curated off-market property list to subscribers: "
            "distressed, pre-foreclosure, probate, tax delinquent, and absentee-owner "
            "properties in Detroit, Memphis, Atlanta, Cleveland, Chicago + 10 other markets. "
            "Each entry includes the property address, owner name + phone, ARV estimate, "
            "and repair tier. You make offers directly — no assignment fee owed to us. "
            "$97/month, cancel anytime."
        ),
        "audience": "buyers",
        "cta": "Reply with your buy box (markets, price range, property type, condition).",
        "hashtags": ["#CashBuyers", "#OffMarket", "#MotivatedSellers", "#REIA", "#FixAndFlip"],
    },
    {
        "title": "Stop chasing the MLS — start working off-market lists",
        "body": (
            "If you're buying rentals or flipping in any of our 15 markets, the leads on "
            "our weekly list are exactly the ones agents never see: behind on payments, "
            "tax delinquent, inherited, absentee. Full owner contact info on every entry. "
            "$97/month gets you the list every week."
        ),
        "audience": "buyers",
        "cta": "Reply 'IN' and your target market and I'll send you a sample list this week.",
        "hashtags": ["#RealEstateInvesting", "#CashBuyers", "#OffMarket", "#BRRRR", "#FixAndFlip"],
    },
]

WHOLESALER_POSTS = [
    {
        "title": "Wholesalers: stop wasting hours analyzing deals manually",
        "body": (
            "Our AI deal analyzer pulls comps, estimates ARV, calculates max offer, and generates "
            "an LOI in under 60 seconds. $197/month, free 7-day trial. Built by wholesalers, for wholesalers."
        ),
        "audience": "wholesalers",
        "cta": f"Reply 'TRIAL' or visit {COMPANY_URL}",
        "hashtags": ["#Wholesaling", "#RealEstateInvesting", "#DealAnalysis", "#PropTech"],
    },
    {
        "title": "Want done-for-you motivated seller outreach?",
        "body": (
            "We run gov-record prospecting + email outreach for wholesalers. You set the markets, "
            "we deliver weekly hot-lead reports. Tiers start at $300/month. No long-term contract."
        ),
        "audience": "wholesalers",
        "cta": "Reply for a sample report from your market.",
        "hashtags": ["#Wholesaling", "#LeadGen", "#RealEstate", "#OutreachAsAService"],
    },
]

ALL_POSTS = SELLER_POSTS + BUYER_POSTS + WHOLESALER_POSTS


def pick_post(audience: str = "") -> dict:
    """Return a random post, optionally filtered by audience."""
    pool = [p for p in ALL_POSTS if not audience or p["audience"] == audience]
    if not pool:
        pool = ALL_POSTS
    post = random.choice(pool).copy()
    post["generated_at"] = datetime.datetime.now().isoformat()
    return post


def format_for_platform(post: dict, platform: str) -> dict:
    """Format a post for a specific platform's character limits and conventions."""
    title = post["title"]
    body  = post["body"]
    cta   = post["cta"]
    tags  = " ".join(post["hashtags"])

    if platform == "x":  # Twitter/X — 280 char hard limit
        text = f"{title}\n\n{cta} {tags}"
        if len(text) > 280:
            text = (title[:200] + "... " + cta + " " + tags)[:280]
        return {"text": text}

    if platform == "reddit":  # title + body, no hashtags
        return {"title": title, "body": f"{body}\n\n{cta}"}

    if platform == "linkedin":  # professional tone, longer body
        return {"text": f"{title}\n\n{body}\n\n{cta}\n\n{tags}"}

    if platform == "pinterest":
        return {"title": title[:100], "description": f"{body[:480]}\n\n{cta}"}

    if platform == "facebook":
        return {"text": f"{title}\n\n{body}\n\n{cta}\n\n{tags}"}

    # default
    return {"text": f"{title}\n\n{body}\n\n{cta}\n\n{tags}"}
