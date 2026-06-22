# New Agent — GitHub Copilot Workspace Template
# Fill in the CAPS fields, then paste the whole file into Copilot Workspace

---

## Project

**Repo name:** AGENT-NAME-here
**Company:** Wholesale Omniverse LLC
**Owner email:** tylumiere25@gmail.com

---

## What to build

Create a new autonomous Python agent called **AGENT_NAME** that does:

> DESCRIBE WHAT THE AGENT DOES IN 2-3 SENTENCES.
> Example: "Scrapes TikTok trending sounds, scores them by virality potential,
> and emails a weekly digest with affiliate product recommendations."

---

## Revenue tiers

| Plan | Price | What they get |
|------|-------|---------------|
| Basic | $PRICE/mo | FEATURE |
| Pro   | $PRICE/mo | FEATURE + FEATURE |
| One-time | $PRICE | FEATURE |

---

## Required file structure

Follow the Wholesale Omniverse agent pattern exactly:

```
AGENT_NAME/
    __init__.py
    tools.py          ← all business logic; expose run_full_cycle() → dict
run_AGENT_NAME_auto.py    ← CLI entry point; calls paywall then run_full_cycle()
```

### tools.py must contain

```python
def run_full_cycle() -> dict:
    """
    Steps:
    1. DATA_SOURCE — describe where data comes from (free scraping / open API / flat file)
    2. PROCESSING — what the agent does with the data
    3. OUTPUT — what it writes to disk (data/XX_outputs/)
    4. EMAIL — send digest to owner via SMTP
    Returns: {"processed": int, "errors": int, "output_path": str}
    """
```

### run_AGENT_NAME_auto.py must contain

```python
from paywall.agent_paywall import paywall_prompt
from autonomous.self_healing import run_with_healing
from AGENT_NAME.tools import run_full_cycle

paywall_prompt("AGENT_NAME", price_monthly=PRICE, price_annual=PRICE*10)

def cycle():
    return run_full_cycle()

if __name__ == "__main__":
    run_with_healing("AGENT_NAME", cycle)
```

---

## Data storage

- All data goes in `data/XX_*/` where XX is the 2-letter agent prefix (e.g. `ab_`)
- Use flat JSON files only — no databases
- Use `autonomous.storage` for atomic reads/writes
- Use `autonomous.mailer` for all email sends (SMTP_USER / SMTP_PASS env vars)
- Use `autonomous.metrics` to record cycle stats

---

## Env vars needed

```bash
# Always available — no setup required
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=WholesaleOmniverse@gmail.com
SMTP_PASS=<gmail-app-password>
AGENT_PASSWORD=<owner-bypass>

# Agent-specific (add any API keys the agent needs)
XX_SPECIFIC_VAR=value
```

---

## Key rules (do not skip)

1. **No API keys in code** — always `os.getenv("VAR_NAME")`
2. **Paywall first** — every `run_*_auto.py` calls `paywall_prompt` before anything else
3. **Self-healing** — every cycle is wrapped with `run_with_healing("key", fn)`
4. **Free sources only** — use web scraping / open data unless user explicitly adds a paid key
5. **Owner bypass** — `AGENT_PASSWORD` env var always skips the paywall
6. **No interactive steps** — the agent must run fully unattended via `python3 run_*_auto.py`
7. **Autonomous.storage** — use `storage.load(path, default)` and `storage.save(path, data)` for all JSON I/O
8. **Prefix all data dirs** — `data/XX_outputs/`, `data/XX_inputs/` using the 2-letter agent prefix

---

## Shared infrastructure to reuse (already in repo)

| Import | What it does |
|--------|-------------|
| `from autonomous import storage` | Atomic JSON load/save |
| `from autonomous import mailer` | Gmail SMTP send |
| `from autonomous import metrics` | Append metrics to agent_metrics.json |
| `from autonomous.self_healing import run_with_healing` | Crash recovery + owner email on 3 failures |
| `from paywall.agent_paywall import paywall_prompt` | Subscription gate |

---

## Optional — Render deployment

If this agent needs to run 24/7 on Render free tier, add to `render.yaml`:

```yaml
  - type: web
    name: AGENT-NAME
    runtime: python
    plan: free
    region: oregon
    healthCheckPath: /health
    buildCommand: pip install -r requirements-AGENT.txt
    startCommand: python3 run_AGENT_NAME_server.py
    envVars:
      - key: AGENT_PASSWORD
        value: owner
      - key: SPECIFIC_KEY
        sync: false
```

And create `run_AGENT_NAME_server.py` using Flask with `/health` and `/status`
endpoints, running the trading/processing loop in a daemon thread.

---

## Example output for a complete cycle

```
[2026-06-22 10:00:00] Starting AGENT_NAME cycle #1
[2026-06-22 10:00:03] Fetched 42 items from DATA_SOURCE
[2026-06-22 10:00:05] Processed: 38 ok, 4 errors
[2026-06-22 10:00:06] Digest written to data/XX_outputs/2026-06-22.md
[2026-06-22 10:00:07] Email sent to WholesaleOmniverse@gmail.com
Cycle result: {"processed": 38, "errors": 4, "output_path": "data/XX_outputs/2026-06-22.md"}
```

---

## Deliverables checklist

- [ ] `AGENT_NAME/__init__.py`
- [ ] `AGENT_NAME/tools.py` with `run_full_cycle() -> dict`
- [ ] `run_AGENT_NAME_auto.py` with paywall + self-healing
- [ ] `requirements-AGENT.txt` (minimal deps only)
- [ ] Entry added to `render.yaml` (if server-based)
- [ ] README section describing the agent (add to CLAUDE.md)
