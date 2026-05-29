#!/usr/bin/env python3
"""Print the new-user activation funnel from the ActivationEvent log.

Read-only. Answers the questions we couldn't before:
  - How many orgs signed up?
  - What share ever added a contact (activation rate)?
  - How long does the first contact take?
  - How many used the dashboard quick-add?

Usage:
    python scripts/activation_report.py

It reports against whatever DATABASE_URL is configured (your .env), so run it
wherever the data you want to measure lives.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app
from services.activation_service import funnel_summary


def _fmt_duration(seconds):
    if seconds is None:
        return "n/a"
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m {sec}s"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h {minutes}m"
    days, hours = divmod(hours, 24)
    return f"{days}d {hours}h"


def main():
    app = create_app()
    with app.app_context():
        s = funnel_summary()

    total = s["total_signups"]
    activated = s["activated"]
    rate = s["activation_rate"] * 100

    print("=" * 48)
    print("  NEW-USER ACTIVATION FUNNEL")
    print("=" * 48)
    print(f"  Signups (orgs)            : {total}")
    print(f"  Added >=1 contact         : {activated}")
    print(f"  Activation rate           : {rate:.1f}%")
    print(f"  Used dashboard quick-add  : {s['quick_add_orgs']}")
    print("-" * 48)
    print(f"  Median time to 1st contact: {_fmt_duration(s['median_seconds_to_first_contact'])}")
    print(f"  Avg time to 1st contact   : {_fmt_duration(s['avg_seconds_to_first_contact'])}")
    print("=" * 48)
    if total == 0:
        print("  (No activation events recorded yet.)")


if __name__ == "__main__":
    main()
