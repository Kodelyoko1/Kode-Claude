#!/usr/bin/env python3
import argparse
from outreach_service.agent import chat, autonomous_run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wholesale Omniverse — Outreach-as-a-Service")
    parser.add_argument("--auto", action="store_true", help="Run autonomous campaign cycle for all clients")
    parser.add_argument("--interval", type=int, default=0, help="Repeat interval in minutes (0 = run once)")
    args = parser.parse_args()

    if args.auto:
        autonomous_run(interval_minutes=args.interval, continuous=args.interval > 0)
    else:
        chat()
