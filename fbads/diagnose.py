"""FBAds preflight."""
from __future__ import annotations
import os, sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fbads.launcher import _have_creds
from fbads.tools import latest_pack, ALL_POSTS, AUDIENCE_TARGETING


@dataclass
class Check:
    name: str; severity: str; status: str; detail: str = ""; fix_hint: str = ""


def check_meta_creds():
    ready, missing = _have_creds()
    if ready:
        token = os.environ.get("META_ACCESS_TOKEN", "")
        acct  = os.environ.get("META_AD_ACCOUNT_ID", "")
        page  = os.environ.get("META_PAGE_ID", "")
        return Check("Meta credentials", "P0", "pass",
                     f"token={len(token)} chars  ad_account={acct}  page={page}")
    return Check("Meta credentials", "P0", "fail",
                 f"missing: {', '.join(missing)}",
                 "See FBADS_SETUP.md for how to get each value")


def check_content_breadth():
    from collections import Counter
    by_aud = Counter(p["audience"] for p in ALL_POSTS)
    covered = set(by_aud) & set(AUDIENCE_TARGETING)
    detail = f"{len(ALL_POSTS)} posts across {len(by_aud)} audiences: " + \
             ", ".join(f"{a}={n}" for a, n in by_aud.most_common())
    if len(covered) < len(AUDIENCE_TARGETING):
        gap = sorted(set(AUDIENCE_TARGETING) - covered)
        return Check("Content breadth", "P1", "warn",
                     detail + f" · gap: {', '.join(gap)}",
                     "Add posts in social_agent/content.py for the gap audiences")
    return Check("Content breadth", "info", "info", detail)


def check_latest_pack():
    pack = latest_pack()
    if not pack:
        return Check("Latest pack", "info", "info",
                     "no packs built yet — run `python3 run_fbads_auto.py --build`")
    return Check("Latest pack", "info", "info",
                 f"{pack['date']} — {pack['total']} ads, "
                 f"${pack['potential_daily_spend']:.0f}/day potential")


def check_higgsfield():
    key = os.environ.get("HIGGSFIELD_API_KEY", "")
    if not key:
        return Check("Higgsfield", "info", "info",
                     "no HIGGSFIELD_API_KEY — use --higgsfield to emit paste-ready prompts")
    return Check("Higgsfield", "info", "info",
                 f"key set ({len(key)} chars); REST push stub awaits documented endpoint")


def check_image_assets():
    """Each ad references an image_hint. Spot-check defaults exist."""
    needed = {"data/logo.png", "data/body_bg.jpg", "data/body_bg2.jpg"}
    missing = [p for p in needed
               if not (Path(__file__).parent.parent / p).exists()]
    if missing:
        return Check("Image assets", "P1", "warn",
                     f"missing: {', '.join(missing)}",
                     "Add image files or change image_hint defaults; "
                     "Meta accepts JPG/PNG, 1080×1080 ideal")
    return Check("Image assets", "info", "info", f"all {len(needed)} default images present")


def run_diagnostics():
    checks = [check_meta_creds(), check_content_breadth(),
              check_latest_pack(), check_higgsfield(), check_image_assets()]
    summary = {"P0_fail": sum(1 for c in checks if c.severity == "P0" and c.status == "fail"),
               "P1_warn": sum(1 for c in checks if c.severity == "P1" and c.status == "warn"),
               "passed":  sum(1 for c in checks if c.status == "pass"),
               "total":   len(checks)}
    summary["ready_to_run"] = summary["P0_fail"] == 0
    return {"checks": [c.__dict__ for c in checks], "summary": summary}


def print_report(r):
    icon = {"pass": "✓", "fail": "✗", "warn": "!", "info": "·"}
    for c in r["checks"]:
        print(f"  [{icon[c['status']]}] [{c['severity']:>4s}] {c['name']:20s} {c['detail']}")
        if c["fix_hint"] and c["status"] in ("fail", "warn"):
            print(f"        ↳ {c['fix_hint']}")
    s = r["summary"]
    print(f"\n  Result: {s['passed']}/{s['total']} passed · P0={s['P0_fail']} · P1={s['P1_warn']}")
    if not s["ready_to_run"]:
        print("  ✗ Meta creds blocked. --build (offline CSV) still works.")


def main():
    print("FBAds preflight\n")
    r = run_diagnostics(); print_report(r)
    return 0 if r["summary"]["ready_to_run"] else 1


if __name__ == "__main__":
    sys.exit(main())
