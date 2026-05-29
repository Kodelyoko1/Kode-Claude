# Affiliate Revenue Setup

The Pinterest auto-poster already has slots for four affiliate programs. Once you sign up and paste your affiliate URLs into `.env`, the bot starts pinning content with your tracking links automatically.

## Why this is the fastest money path on Pinterest

- No audience needed — pins rank on search intent
- Commissions paid even on free trials (most programs)
- $30–$200 per signup, monthly recurring on some
- Total time investment to set up: ~30 minutes

## The four programs to sign up for

| Program | Commission | Conversion target | Sign up |
|---|---|---|---|
| **BatchSkipTracing** | $0.30–$1 per skip-trace credit purchased (recurring) | Wholesalers needing seller phone numbers | https://batchskiptracing.com/affiliate |
| **Carrot** | $30 first month + 10% recurring | Wholesalers wanting investor landing pages | https://carrot.com/affiliates/ |
| **PropStream** | $40 per paid signup (after free trial) | Wholesalers building seller lists | https://www.propstream.com/affiliate-program |
| **REISift** | $50 + 30% lifetime recurring | Wholesalers with 1k+ leads needing CRM | https://reisift.io/affiliates |

(Links may have changed — search "[product name] affiliate program" if any 404.)

## How to wire each one in

After you create an account and get approved (usually instant or within 24h), each program gives you a unique tracking URL. Paste them into `.env`:

```
# ─── Affiliate URLs ───────────────────────────────────────────────────────────
AFFILIATE_BATCHSKIPTRACING_URL=https://batchskiptracing.com/?ref=YOUR_ID
AFFILIATE_CARROT_URL=https://oncarrot.com/?ref=YOUR_ID
AFFILIATE_PROPSTREAM_URL=https://app.propstream.com/login?affiliate=YOUR_ID
AFFILIATE_REISIFT_URL=https://reisift.io/?fpr=YOUR_ID
```

Once any of those are set, `run_pinterest_auto.py` will rotate them into the daily pin set automatically. No code change.

## Realistic earnings projection (per program)

Assuming:
- 1 affiliate pin/day = 30 pins/month per program
- Each pin gets 500–2,000 impressions over 6 months (Pinterest's compounding effect)
- 1% click-through rate
- 5% trial signup
- 25% trial → paid conversion

**Per program in month 6:**
- ~50,000 impressions
- 500 clicks
- 25 trial signups
- 6 paid conversions × $40 avg = **$240/program/month recurring**

**With all 4 programs running:** ~$1,000/mo passive recurring by month 6.

That's not life-changing. But it's revenue with zero ongoing effort once set up, while you focus on closing actual wholesale deals.

## What to do tonight

1. Sign up for the affiliate program that fits your audience best — I'd pick **Carrot** first (their tool is the most universally useful for wholesalers, so it converts best)
2. Once approved, grab your tracking URL
3. Add `AFFILIATE_CARROT_URL=...` to `.env`
4. Run `python3 run_pinterest_auto.py --type affiliate --dry-run` to preview

Once Pinterest itself is set up (`python3 setup_pinterest.py`), the nightly cron will start pinning automatically.
