#!/usr/bin/env python3
import sys
import os
import getpass
import argparse
from pathlib import Path
from dotenv import load_dotenv

env_file = Path(__file__).parent / ".env"
if env_file.exists():
    load_dotenv(env_file)

# Owner auth via env var or password prompt
if os.environ.get("AGENT_PASSWORD") == "0923":
    pass
else:
    password = getpass.getpass("Enter owner password (or press Enter to subscribe as client): ").strip()
    if password == "0923":
        os.environ["AGENT_PASSWORD"] = "0923"
    elif password == "":
        # Not the owner — check client paywall
        from paywall.agent_paywall import paywall_prompt
        if not paywall_prompt("wholesale"):
            sys.exit(0)
    else:
        print("Incorrect password. Access denied.")
        sys.exit(1)

(Path(__file__).parent / "data").mkdir(exist_ok=True)

if not os.environ.get("ANTHROPIC_API_KEY"):
    print("Error: ANTHROPIC_API_KEY is not set.")
    print("Add it to your .env file: ANTHROPIC_API_KEY=your_key_here")
    sys.exit(1)

parser = argparse.ArgumentParser(description="Wholesale Omniverse Agent")
parser.add_argument("--auto", action="store_true", help="Run autonomous prospecting cycle")
parser.add_argument("--cities", nargs="+", metavar="CITY_STATE", help="Cities to target (e.g. 'Detroit MI' 'Baltimore MD')")
parser.add_argument("--interval", type=int, default=0, metavar="MINUTES", help="Repeat every N minutes (use with --auto --continuous)")
parser.add_argument("--continuous", action="store_true", help="Loop autonomously on --interval schedule")
args = parser.parse_args()

from agent import chat, autonomous_run

if __name__ == "__main__":
    if args.auto:
        autonomous_run(cities=args.cities, interval_minutes=args.interval, continuous=args.continuous)
    else:
        chat()
