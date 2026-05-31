#!/usr/bin/env python3
"""
ViralRecycler SaaS HTTP server — Flask-free, stdlib only.

Routes:
    GET  /                                → marketing site (viral_recycler.html)
    GET  /viral-recycler                  → same
    GET  /viral-recycler/signup           → signup page
    GET  /viral-recycler/dashboard?key=…  → customer dashboard
    POST /api/trial/start                 → create trial, return access_key
    GET  /api/customer/status?key=…       → trial status + recent uploads
    POST /api/customer/submit             → queue a URL for processing
    GET  /privacy.html                    → static
    GET  /assets/*                        → static

Background worker thread drains every customer's queue every 60s.

Run:
    python3 viral_recycler_server.py --port 8080
"""
import argparse
import json
import threading
import time
import sys
import os
import urllib.parse
from datetime import datetime
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, str(Path(__file__).parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from paywall import trial
from autonomous import storage, mailer, metrics

WEBSITE_DIR = Path(__file__).parent / "website"
DATA_DIR = Path(__file__).parent / "data"

# Per-customer queue storage: data/vr_customers/{key}/queue.json
CUSTOMERS_DIR = DATA_DIR / "vr_customers"


def _customer_dir(key: str) -> Path:
    d = CUSTOMERS_DIR / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def _customer_queue(key: str) -> list:
    p = _customer_dir(key) / "queue.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []


def _save_customer_queue(key: str, queue: list):
    (_customer_dir(key) / "queue.json").write_text(json.dumps(queue, indent=2))


def _customer_uploads(key: str) -> list:
    p = _customer_dir(key) / "uploads.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return []
    return []


def _append_customer_upload(key: str, record: dict):
    p = _customer_dir(key) / "uploads.json"
    existing = _customer_uploads(key)
    existing.append(record)
    p.write_text(json.dumps(existing, indent=2))


def background_worker():
    """Drain every customer's queue at ~60s intervals."""
    from viral_recycler.tools import process_one

    while True:
        try:
            CUSTOMERS_DIR.mkdir(parents=True, exist_ok=True)
            for cust_dir in CUSTOMERS_DIR.iterdir():
                if not cust_dir.is_dir():
                    continue
                key = cust_dir.name
                t = trial.check_trial(key)
                if not t.get("allowed"):
                    continue
                queue = _customer_queue(key)
                if not queue:
                    continue
                next_item = None
                for i, item in enumerate(queue):
                    if item.get("status") in (None, "queued"):
                        next_item = (i, item)
                        break
                if not next_item:
                    continue
                idx, item = next_item
                item["status"] = "processing"
                item["processing_started_at"] = datetime.now().isoformat()
                queue[idx] = item
                _save_customer_queue(key, queue)

                try:
                    result = process_one(item)
                except Exception as e:
                    result = {"error": f"unhandled: {e}", "stage": "worker"}

                if "error" in result:
                    item["status"] = "error"
                    item["error"] = result.get("error", "")
                else:
                    item["status"] = "done"
                    item["youtube_url"] = result.get("youtube", {}).get("shorts_url", "")
                    _append_customer_upload(key, result)
                queue[idx] = item
                _save_customer_queue(key, queue)
                metrics.record("viral_recycler",
                               videos_processed=1 if "error" not in result else 0,
                               errors=1 if "error" in result else 0)
        except Exception as e:
            print(f"[worker] {e}", file=sys.stderr)
        time.sleep(60)


def trial_reminders_thread():
    """Send 7d / 1d trial-expiry reminders + PayPal upsell."""
    from paywall.agent_paywall import _price
    while True:
        try:
            for t in trial.trials_needing_reminder():
                price = _price(t.get("agent", "viral_recycler"))
                if t["kind"] == "7d":
                    subject = "7 days left on your ViralRecycler trial"
                    body = (
                        f"Hi {t.get('name', 'there')},\n\n"
                        f"Your free trial wraps in 7 days. If the agent's been useful, "
                        f"continue for ${price}/mo: paypal.me/wholesaleomniverse/{price:.0f}\n\n"
                        f"Reply with your PayPal email after paying and I'll switch you to paid.\n\n"
                        f"— Wholesale Omniverse"
                    )
                else:
                    subject = "Trial ends tomorrow — keep your channel growing"
                    body = (
                        f"Hi {t.get('name', 'there')},\n\n"
                        f"Last day of the free trial. The agent has processed your queue every "
                        f"60 seconds for 30 days.\n\n"
                        f"Keep going: ${price}/mo → paypal.me/wholesaleomniverse/{price:.0f}\n\n"
                        f"Or do nothing and the queue freezes tomorrow.\n\n"
                        f"— Wholesale Omniverse"
                    )
                r = mailer.send("viral_recycler", t["email"], subject, body, purpose="billing")
                if r.get("status") == "sent":
                    trial.mark_reminder_sent(t["access_key"], t["kind"])
        except Exception as e:
            print(f"[reminders] {e}", file=sys.stderr)
        time.sleep(3600 * 6)  # check every 6h


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # quiet default access log
        return

    def _send(self, code: int, body: bytes, content_type: str = "text/html"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _send_file(self, path: Path, content_type: str = "text/html"):
        if not path.exists():
            return self._send(404, b"Not found", "text/plain")
        self._send(200, path.read_bytes(), content_type)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = urllib.parse.parse_qs(parsed.query)

        if path in ("/", "/viral-recycler", "/index.html"):
            return self._send_file(WEBSITE_DIR / "viral_recycler.html")
        if path == "/viral-recycler/signup":
            return self._send_file(WEBSITE_DIR / "viral_recycler_signup.html")
        if path == "/viral-recycler/dashboard":
            return self._send_file(WEBSITE_DIR / "viral_recycler_dashboard.html")
        if path == "/privacy.html":
            return self._send_file(WEBSITE_DIR / "privacy.html")

        if path == "/api/customer/status":
            key = (qs.get("key") or [""])[0]
            if not key:
                return self._send_json(400, {"error": "no key"})
            t = trial.check_trial(key)
            queue = _customer_queue(key)
            uploads = _customer_uploads(key)[-20:][::-1]
            pending = sum(1 for q in queue if q.get("status") in (None, "queued", "processing"))
            youtube_posts = sum(1 for u in uploads if u.get("youtube", {}).get("status") == "uploaded")
            tiktok_posts = sum(1 for u in uploads if u.get("tiktok", {}).get("status") in ("uploaded", "handed_off"))
            return self._send_json(200, {
                "status":         t.get("trial", {}).get("status") if t.get("allowed") else "expired",
                "days_left":      t.get("days_left"),
                "payment_url":    t.get("payment_url", ""),
                "processed":      len(uploads),
                "pending":        pending,
                "youtube_posts":  youtube_posts,
                "tiktok_posts":   tiktok_posts,
                "uploads":        uploads,
            })

        # Static assets
        if path.startswith("/assets/"):
            return self._send_file(WEBSITE_DIR / path.lstrip("/"),
                                   content_type="application/octet-stream")

        return self._send(404, b"Not found", "text/plain")

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path == "/api/trial/start":
            body = self._read_json()
            name = (body.get("name") or "").strip()
            email = (body.get("email") or "").strip()
            agent = body.get("agent", "viral_recycler")
            niche = body.get("niche", "motivational")
            tier = body.get("tier", "pro")
            if not (name and email):
                return self._send_json(400, {"error": "name and email required"})
            record = trial.start_trial(agent, name, email, source="website")
            if "error" in record and record["error"] == "already_signed_up":
                return self._send_json(200, {
                    "access_key": record["access_key"],
                    "status": record.get("status", ""),
                    "note": "existing trial",
                })
            # Persist niche + tier on the customer
            cdir = _customer_dir(record["access_key"])
            (cdir / "profile.json").write_text(json.dumps({
                "name": name, "email": email, "niche": niche,
                "default_tier": tier,
            }, indent=2))
            # Welcome email
            mailer.send(
                agent, email,
                "Your ViralRecycler trial is live",
                f"Hi {name},\n\n"
                f"Your 30-day free trial is active. Access key:\n  {record['access_key']}\n\n"
                f"Dashboard: open the link below and bookmark it.\n\n"
                f"You can drop URLs and they'll be processed every 60 seconds.\n\n"
                f"— Wholesale Omniverse",
                purpose="notification",
            )
            return self._send_json(200, {"access_key": record["access_key"]})

        if path == "/api/customer/submit":
            body = self._read_json()
            key = body.get("key", "")
            url = (body.get("url") or "").strip()
            niche = body.get("niche", "motivational")
            tier = body.get("quality_tier", "pro")
            allow = bool(body.get("allow_copyrighted"))
            if not (key and url):
                return self._send_json(400, {"error": "key and url required"})
            t = trial.check_trial(key)
            if not t.get("allowed"):
                return self._send_json(402, {"error": "trial expired or unknown key",
                                              "payment_url": t.get("payment_url", "")})
            queue = _customer_queue(key)
            queue.append({
                "url":  url,
                "niche": niche,
                "quality_tier": tier,
                "allow_copyrighted": allow,
                "status": "queued",
                "submitted_at": datetime.now().isoformat(),
            })
            _save_customer_queue(key, queue)
            return self._send_json(200, {"queued": True, "position": len(queue)})

        return self._send(404, b"Not found", "text/plain")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=int(os.environ.get("VR_PORT", 8080)))
    parser.add_argument("--no-worker", action="store_true", help="Skip the background processing thread")
    args = parser.parse_args()

    if not args.no_worker:
        threading.Thread(target=background_worker, daemon=True).start()
        threading.Thread(target=trial_reminders_thread, daemon=True).start()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"ViralRecycler SaaS running at http://localhost:{args.port}")
    print(f"  Marketing:  http://localhost:{args.port}/")
    print(f"  Signup:     http://localhost:{args.port}/viral-recycler/signup")
    print(f"  Dashboard:  http://localhost:{args.port}/viral-recycler/dashboard?key=YOUR_KEY")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping…")


if __name__ == "__main__":
    main()
