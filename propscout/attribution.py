"""
PropScout → shared leads.json attribution.

Every prospect from acquire_cycle goes through parent_tools.save_lead(), which
writes to data/leads.json but doesn't know who's calling it — the lead_source
field ends up empty. deal_analyzer can't then credit PropScout for the leads
it produces, and the pipeline-attribution check in diagnose.py shows 0
PropScout-tagged leads despite hundreds of PropScout-motivation leads.

Two operations live here:

  tag_new_prospects(prospects, city, state, record_type)
    Called from acquire_cycle right after the parent helper runs. Matches by
    (address, city, state) and stamps lead_source=PropScout + ps_record_type
    on matching leads that don't already have an attribution.

  backfill()
    One-shot owner command. Walks every motivation-eligible lead with an
    empty lead_source and attributes it to PropScout. Safe to re-run.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR    = Path(__file__).parent.parent / "data"
LEADS_FILE  = DATA_DIR / "leads.json"

PS_MOTIVATIONS = {"tax_delinquent", "code_violations", "vacant", "foreclosure", "probate"}


def _load(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return default


def _save(path: Path, data) -> None:
    path.parent.mkdir(exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.unlink(tmp)
        except OSError: pass
        raise


def _norm(s: str) -> str:
    return (s or "").strip().lower()


def tag_new_prospects(prospects: list, city: str, state: str, record_type: str) -> int:
    """Stamp lead_source=PropScout on leads matching this cell's prospects.

    Match key: (lower address, lower city, upper state). Only attributes
    leads that don't already have a lead_source — never overwrites HUDScout,
    a manual attribution, etc.
    """
    if not prospects:
        return 0
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return 0

    targets = {
        (_norm(p.get("address", "")), _norm(city), state.upper())
        for p in prospects if p.get("address")
    }
    if not targets:
        return 0

    tagged = 0
    for lead in leads.values():
        if lead.get("lead_source"):
            continue
        key = (_norm(lead.get("address", "")), _norm(lead.get("city", "")),
               (lead.get("state") or "").upper())
        if key in targets:
            lead["lead_source"]    = "PropScout"
            lead["ps_record_type"] = record_type
            lead["ps_tagged_at"]   = datetime.now().isoformat()
            tagged += 1

    if tagged:
        _save(LEADS_FILE, leads)
    return tagged


def backfill() -> dict:
    """Attribute existing motivation-eligible untagged leads to PropScout.

    Conservative match: motivation must be in PS_MOTIVATIONS and lead_source
    must be empty/missing. Doesn't touch leads that already have any source
    (HUDScout, manual import, etc.).
    """
    leads = _load(LEADS_FILE, {})
    if not isinstance(leads, dict):
        return {"error": "leads.json wrong shape", "tagged": 0}

    tagged = 0
    by_motivation = {}
    now = datetime.now().isoformat()
    for lead in leads.values():
        if lead.get("lead_source"):
            continue
        m = lead.get("motivation", "")
        if m not in PS_MOTIVATIONS:
            continue
        lead["lead_source"]      = "PropScout"
        lead["ps_record_type"]   = m
        lead["ps_tagged_at"]     = now
        lead["ps_backfilled"]    = True
        by_motivation[m] = by_motivation.get(m, 0) + 1
        tagged += 1

    if tagged:
        _save(LEADS_FILE, leads)
    return {"tagged": tagged, "by_motivation": by_motivation}


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="PropScout lead attribution")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("backfill", help="Tag existing motivation-eligible untagged leads as PropScout")
    args = p.parse_args()
    if args.cmd == "backfill":
        out = backfill()
        print(json.dumps(out, indent=2))


if __name__ == "__main__":
    _cli()
