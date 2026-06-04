"""
ShowNotes health: per-episode outcomes + SRT parse log + Claude probe.

ShowNotes has TWO input sources: data/sn_inputs/*.txt (owner-dropped)
and data/tr_outputs/*.txt (auto-chained from the Transcribe agent).
build_queue scans both, dedupes by slug, runs build_show_notes for each
transcript >200 chars, and emits markdown to data/sn_outputs/.
fulfill_cycle emails *new* notes to active subscribers, tracking
per-email delivery in sn_delivery_log.json.

Several silent failure modes the existing run_full_cycle doesn't surface:

  1. Both input dirs empty / stale → 0 produced, no alert
  2. The Transcribe chain is idle (tr_outputs/ stops getting new files)
     but the owner doesn't notice because sn_inputs/ might still have
     a few stragglers — quality silently degrades
  3. ANTHROPIC_API_KEY set but invalid or out-of-credit → Claude TL;DR
     silently swallows the exception and falls back to heuristic.
     Customer is paying for the LLM tier and getting the free tier.
  4. SRT files exist next to .txt but the format is malformed (Whisper
     timestamp drift) → _parse_srt_timestamps returns [] → chapters
     missing without any indication
  5. Transcripts < 200 chars → build_queue silently skips
  6. mail_failed silent retry-loop (same as careerforge/pantrychef)

This module tracks per-episode outcomes, a separate SRT outcome log,
and a probe_anthropic() helper that the diagnose check uses to verify
the LLM path actually works (vs just being silently swallowed).

State files:
  data/sn_episode_log.json   — rolling per-episode outcome log
  data/sn_srt_log.json       — per-episode SRT parse outcomes (when .srt present)
  data/sn_delivery_outcomes.json — per-attempt mailer outcomes (for stuck detection)

Env:
  SN_LOG_MAX               default 300 — cap on rolling histories
  SN_MIN_TRANSCRIPT_CHARS  default 200 — matches the build_queue gate
  SN_TR_CHAIN_STALE_DAYS   default 21  — tr_outputs/ "idle" threshold for P1
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

DATA_DIR     = Path(__file__).parent.parent / "data"
INPUTS_DIR   = DATA_DIR / "sn_inputs"
TR_OUTPUTS   = DATA_DIR / "tr_outputs"
OUTPUTS_DIR  = DATA_DIR / "sn_outputs"
EPISODE_LOG  = DATA_DIR / "sn_episode_log.json"
SRT_LOG      = DATA_DIR / "sn_srt_log.json"
DELIVERY_LOG = DATA_DIR / "sn_delivery_outcomes.json"

LOG_MAX               = int(os.environ.get("SN_LOG_MAX", "300"))
MIN_TRANSCRIPT_CHARS  = int(os.environ.get("SN_MIN_TRANSCRIPT_CHARS", "200"))
TR_CHAIN_STALE_DAYS   = int(os.environ.get("SN_TR_CHAIN_STALE_DAYS", "21"))

VALID_EPISODE_OUTCOMES = {"success", "too_short", "build_failed"}
VALID_SRT_OUTCOMES     = {"parsed", "no_srt", "malformed"}
VALID_DELIVERY_OUTCOMES = {"success", "mail_failed", "no_email"}


def _now() -> str:
    return datetime.now().isoformat()


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


def _month_key(ts: str = "") -> str:
    return ts[:7] if ts else datetime.now().strftime("%Y-%m")


def _append_capped(path: Path, entry: dict) -> None:
    log = _load(path, [])
    if not isinstance(log, list):
        log = []
    log.append(entry)
    if len(log) > LOG_MAX:
        log = log[-LOG_MAX:]
    _save(path, log)


# ─────────────────────────── Per-episode outcomes ───────────────────────────

def record_episode(slug: str, outcome: str, source: str = "",
                   detail: str = "") -> None:
    """outcome ∈ {success, too_short, build_failed}.
    source ∈ {sn_inputs, tr_outputs, ""}."""
    if not slug:
        return
    _append_capped(EPISODE_LOG, {
        "ts": _now(), "slug": slug, "outcome": outcome,
        "source": source or "", "detail": detail or "",
    })


def episode_outcome_summary() -> dict:
    log = _load(EPISODE_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_EPISODE_OUTCOMES},
                "by_source": {}}
    counts = {oc: 0 for oc in VALID_EPISODE_OUTCOMES}
    by_source: dict[str, int] = {}
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        s = r.get("source", "?")
        by_source[s] = by_source.get(s, 0) + 1
    return {"total": len(log), **counts, "by_source": by_source}


def recent_episodes(limit: int = 50) -> list[dict]:
    log = _load(EPISODE_LOG, [])
    if not isinstance(log, list):
        return []
    return log[-limit:][::-1]


# ─────────────────────────── SRT parse outcomes ───────────────────────────

def record_srt(slug: str, outcome: str, entries: int = 0, detail: str = "") -> None:
    """outcome ∈ {parsed, no_srt, malformed}."""
    if not slug:
        return
    _append_capped(SRT_LOG, {
        "ts": _now(), "slug": slug, "outcome": outcome,
        "entries": int(entries), "detail": detail or "",
    })


def srt_outcome_summary() -> dict:
    log = _load(SRT_LOG, [])
    if not isinstance(log, list) or not log:
        return {"total": 0, **{oc: 0 for oc in VALID_SRT_OUTCOMES},
                "malformed_recent": []}
    counts = {oc: 0 for oc in VALID_SRT_OUTCOMES}
    malformed = []
    for r in log:
        oc = r.get("outcome", "")
        if oc in counts:
            counts[oc] += 1
        if oc == "malformed":
            malformed.append(r.get("slug", "?"))
    return {"total": len(log), **counts,
            "malformed_recent": malformed[-10:]}


# ─────────────────────────── Delivery outcomes ───────────────────────────

def record_delivery(email: str, outcome: str, slugs: int = 0,
                    detail: str = "") -> None:
    """outcome ∈ {success, mail_failed, no_email}. slugs = how many .md
    files would have been delivered in this attempt."""
    if not email:
        return
    _append_capped(DELIVERY_LOG, {
        "ts": _now(), "email": email.lower(), "outcome": outcome,
        "slugs": int(slugs), "detail": detail or "",
    })


def stuck_mail_failed(min_attempts: int = 3) -> list[dict]:
    log = _load(DELIVERY_LOG, [])
    if not isinstance(log, list):
        return []
    by_email: dict[str, dict] = {}
    for r in log:
        if r.get("outcome") != "mail_failed":
            continue
        e = r.get("email", "")
        if not e:
            continue
        rec = by_email.setdefault(e, {"attempts": 0, "last_ts": "", "last_detail": ""})
        rec["attempts"] += 1
        rec["last_ts"] = r.get("ts", "")
        rec["last_detail"] = r.get("detail", "")
    return sorted(
        [{"email": e, **rec} for e, rec in by_email.items() if rec["attempts"] >= min_attempts],
        key=lambda r: -r["attempts"],
    )


def monthly_deliveries_per_email(month: str = "") -> dict[str, int]:
    """Count delivered (outcome=success) attempts per email in the given month.
    Used for monthly_99 cap (4/mo) enforcement check."""
    month = month or _month_key()
    log = _load(DELIVERY_LOG, [])
    if not isinstance(log, list):
        return {}
    counts: dict[str, int] = {}
    for r in log:
        if r.get("outcome") != "success":
            continue
        if _month_key(r.get("ts", "")) != month:
            continue
        e = r.get("email", "")
        if not e:
            continue
        # Each delivery batch covers `slugs` shownotes; for cap purposes count
        # individual shownotes delivered, not batches.
        counts[e] = counts.get(e, 0) + max(int(r.get("slugs", 1)), 1)
    return counts


# ─────────────────────────── Probes ───────────────────────────

def probe_anthropic() -> dict:
    """Verify the optional Claude path actually works. Returns
    {"enabled": bool, "ok": bool, "error": "..."}.

    When enabled=False the LLM path is intentionally skipped — that's
    not a fault. When enabled=True but ok=False, customers paying for
    the "LLM TL;DR" experience are silently getting the heuristic
    fallback."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return {"enabled": False, "ok": True, "detail": "ANTHROPIC_API_KEY unset — heuristic mode"}
    try:
        import anthropic
    except ImportError:
        return {"enabled": True, "ok": False,
                "error": "ANTHROPIC_API_KEY set but `anthropic` package not importable"}
    try:
        client = anthropic.Anthropic()
        # Cheapest possible call — uses haiku to keep cost minimal
        msg = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=8,
            messages=[{"role": "user", "content": "ok"}],
        )
        return {"enabled": True, "ok": True,
                "detail": f"haiku probe → {len(msg.content[0].text)} chars"}
    except Exception as e:
        return {"enabled": True, "ok": False,
                "error": f"{type(e).__name__}: {str(e)[:160]}"}


def probe_inputs() -> dict:
    """Triangulate both input sources + sn_outputs + Transcribe-chain age.

    Returns {
      "ok": bool,
      "sn_inputs": N,
      "tr_outputs": N,
      "sn_outputs": N,
      "tr_outputs_newest_age_days": N|None,
      "sn_inputs_newest_age_days":  N|None,
      "candidates":  N,  — unique-by-slug from both sources
      "tr_chain_idle": bool,
    }
    """
    def _count_and_age(d: Path):
        if not d.exists():
            return 0, None
        files = [f for f in d.glob("*.txt") if f.is_file()]
        if not files:
            return 0, None
        newest = max(f.stat().st_mtime for f in files)
        return len(files), (datetime.now() - datetime.fromtimestamp(newest)).days

    sn_n, sn_age = _count_and_age(INPUTS_DIR)
    tr_n, tr_age = _count_and_age(TR_OUTPUTS)

    sn_outputs = 0
    if OUTPUTS_DIR.exists():
        sn_outputs = sum(1 for _ in OUTPUTS_DIR.glob("*.md"))

    # Unique candidates by slug — matches tools._source_candidates logic
    seen = set()
    for d in (INPUTS_DIR, TR_OUTPUTS):
        if not d.exists():
            continue
        for f in d.glob("*.txt"):
            seen.add(f.stem)

    tr_idle = tr_age is None or tr_age > TR_CHAIN_STALE_DAYS

    return {
        "ok":                          (sn_n + tr_n) > 0,
        "sn_inputs":                   sn_n,
        "tr_outputs":                  tr_n,
        "sn_outputs":                  sn_outputs,
        "tr_outputs_newest_age_days":  tr_age,
        "sn_inputs_newest_age_days":   sn_age,
        "candidates":                  len(seen),
        "tr_chain_idle":               tr_idle,
        "tr_chain_stale_days":         TR_CHAIN_STALE_DAYS,
        "min_transcript_chars":        MIN_TRANSCRIPT_CHARS,
    }


def _cli():
    import argparse
    p = argparse.ArgumentParser(description="ShowNotes health + probes")
    p.add_argument("--probe",    action="store_true",
                   help="Triangulate both input sources + outputs")
    p.add_argument("--anthropic", action="store_true",
                   help="Probe the ANTHROPIC_API_KEY path with a cheap haiku call")
    p.add_argument("--episodes", type=int, default=0,
                   help="Show last N per-episode outcomes")
    p.add_argument("--srt", action="store_true",
                   help="SRT parse outcome summary")
    p.add_argument("--deliveries", action="store_true",
                   help="Stuck mail_failed summary")
    p.add_argument("--usage", action="store_true",
                   help="Per-email shownotes delivered this month")
    args = p.parse_args()
    if args.probe:
        print(json.dumps(probe_inputs(), indent=2))
        return
    if args.anthropic:
        print(json.dumps(probe_anthropic(), indent=2))
        return
    if args.episodes:
        for r in recent_episodes(args.episodes):
            print(f"  {r['ts'][:19]}  {r['outcome']:<12s}  "
                  f"src={r['source']:<10s}  {r['slug']}")
        s = episode_outcome_summary()
        print(f"\n  log_total={s['total']}  success={s['success']}  "
              f"too_short={s['too_short']}  build_failed={s['build_failed']}")
        if s["by_source"]:
            print(f"  by_source: " + ", ".join(f"{k}={v}" for k, v in s["by_source"].items()))
        return
    if args.srt:
        s = srt_outcome_summary()
        print(f"  total={s['total']}  parsed={s['parsed']}  no_srt={s['no_srt']}  "
              f"malformed={s['malformed']}")
        if s["malformed_recent"]:
            print(f"  recent malformed slugs: {', '.join(s['malformed_recent'])}")
        return
    if args.deliveries:
        stuck = stuck_mail_failed()
        if not stuck:
            print("(no emails with ≥3 mail_failed attempts)")
        else:
            for r in stuck:
                print(f"  {r['email']}  {r['attempts']}× attempts  "
                      f"last_detail={r['last_detail'][:60]}")
        return
    if args.usage:
        usage = monthly_deliveries_per_email()
        if not usage:
            print("(no deliveries recorded this month)")
        else:
            print(f"{'EMAIL':<40s}  {'SHOWNOTES':>9s}")
            for e, n in sorted(usage.items(), key=lambda kv: -kv[1]):
                print(f"  {e:<40s}  {n:>9d}")
        return
    s = episode_outcome_summary()
    print(f"  episode log: total={s['total']}  success={s['success']}  "
          f"too_short={s['too_short']}  build_failed={s['build_failed']}")
    sr = srt_outcome_summary()
    if sr["total"]:
        print(f"  srt log:     total={sr['total']}  parsed={sr['parsed']}  "
              f"malformed={sr['malformed']}")


if __name__ == "__main__":
    _cli()
