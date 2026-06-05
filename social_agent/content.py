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

CREATOR_POSTS = [
    {
        "title": "Your sales page is leaking conversions. Find out in 90 seconds.",
        "body": (
            "Free scan of any Gumroad / Payhip / Sellfy / Ko-fi page: CTA clarity, "
            "social proof, copy length, mobile viewport, trust signals, pricing visibility. "
            "Score out of 100 + your top 3 fixes — emailed within 24h. Upgrade to the full "
            "audit ($77 one-time) for every issue with specific copy + layout fixes."
        ),
        "audience": "creators",
        "cta": "Drop your page link in a DM and I'll send the scan back.",
        "hashtags": ["#Gumroad", "#Payhip", "#IndieMaker", "#Creator", "#SaaS"],
    },
    {
        "title": "Why your $9 product page converts at 0.4%",
        "body": (
            "Most indie sales pages miss the same 3-4 things: no visible price above the fold, "
            "0 testimonials, copy under 200 words, no mobile viewport tag. Each one shaves "
            "5-15% off conversion. Free audit, 24h turnaround, you get the score + top 3 fixes."
        ),
        "audience": "creators",
        "cta": "Drop your link — free audit, no signup.",
        "hashtags": ["#Gumroad", "#IndieHackers", "#ConversionRate", "#SaaS", "#DTC"],
    },
]

JOBSEEKER_POSTS = [
    {
        "title": "Your resume is being filtered by an ATS before a human sees it.",
        "body": (
            "Recruiters use applicant tracking systems that scan for exact-match keywords from "
            "the job description. Generic resumes get auto-rejected. Send me a job description "
            "+ your current resume and I'll send back a tailored version in 24h: rewritten profile, "
            "reordered experience, ATS match report. $29 per job. $49/mo unlimited."
        ),
        "audience": "jobseekers",
        "cta": "DM the job link + your current resume. First one free.",
        "hashtags": ["#JobSearch", "#Resume", "#ATS", "#CareerAdvice", "#Hiring"],
    },
    {
        "title": "Most resumes get auto-rejected before a human reads them.",
        "body": (
            "If you've sent 50+ applications and heard nothing back, the issue is almost certainly "
            "your resume failing the ATS keyword scan, not your background. Paste a job link "
            "and I'll show you exactly which required keywords your resume is missing — free."
        ),
        "audience": "jobseekers",
        "cta": "DM the job link.",
        "hashtags": ["#JobSearch", "#TechJobs", "#Hiring", "#Resume", "#CareerAdvice"],
    },
]

PODCASTER_POSTS = [
    {
        "title": "Stop manually transcribing your podcasts.",
        "body": (
            "Drop me your raw episode audio and you'll get back a clean .txt + .srt within 24h. "
            "$19/episode one-off, $79/mo for 10 hours of audio, $297 for a 30-episode bulk pack. "
            "Subtitles drop straight into Premiere / CapCut / YouTube. First one free."
        ),
        "audience": "podcasters",
        "cta": "Reply with an episode link — first transcript free.",
        "hashtags": ["#Podcasting", "#Podcaster", "#PodcastEditing", "#ContentCreator"],
    },
    {
        "title": "Get show notes for your podcast — TL;DR + chapters + SEO pack",
        "body": (
            "Send me a transcript (or raw audio + we'll transcribe it first) and you'll get back "
            "structured show notes within 24h: 2-sentence TL;DR, 5 key takeaways, chapter timestamps, "
            "resource links, SEO title + description. $29/ep, $99/mo for 4 episodes, $297/mo unlimited."
        ),
        "audience": "podcasters",
        "cta": "Reply with an episode and I'll send a sample.",
        "hashtags": ["#Podcasting", "#ShowNotes", "#PodcastSEO", "#ContentMarketing"],
    },
]

LOCAL_BIZ_POSTS = [
    {
        "title": "Negative reviews are losing you customers right now.",
        "body": (
            "We draft thoughtful reply templates for every negative review on Google + Yelp — "
            "your voice, accountable tone, never defensive. $79/mo per location: weekly batch of "
            "drafts ready to copy-paste. One-time deep audit available for $497. Tested across "
            "200+ small businesses."
        ),
        "audience": "local_biz",
        "cta": "DM your business name + city for a free sample reply draft.",
        "hashtags": ["#SmallBusiness", "#ReputationManagement", "#GoogleReviews", "#LocalSEO"],
    },
]

ALL_POSTS = (SELLER_POSTS + BUYER_POSTS + WHOLESALER_POSTS +
             CREATOR_POSTS + JOBSEEKER_POSTS + PODCASTER_POSTS + LOCAL_BIZ_POSTS)


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
