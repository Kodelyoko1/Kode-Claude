#!/usr/bin/env python3
import argparse
from followup_agent.agent import chat, autonomous_run

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seller Follow-Up Sequence Agent")
    parser.add_argument("--auto", action="store_true", help="Run autonomous follow-up cycle")
    parser.add_argument("--interval", type=int, default=0, help="Repeat every N minutes (0 = once)")
    args = parser.parse_args()

    if args.auto:
        autonomous_run(interval_minutes=args.interval, continuous=args.interval > 0)
    else:
        chat()
