#!/usr/bin/env python3
"""Print the new-user retention funnel from the ActivationEvent log.

Read-only. User-level metrics with eligible denominators.

Usage:
    python scripts/activation_report.py
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

    print("=" * 56)
    print("  NEW-USER RETENTION FUNNEL (user-level)")
    print("=" * 56)
    print(f"  Signups (users)              : {total}")
    print(f"  Eligible for 24h activation  : {s['eligible_activation_signups']}")
    print(f"  Still observing (<24h)       : {s['activation_observing']}")
    print(f"  Activated within 24h         : {activated}")
    print(f"  24h activation rate          : {rate:.1f}%")
    print(f"  Eligible for D7 return       : {s['eligible_d7_signups']}")
    print(f"  D2-D7 return rate            : {s['d7_return_rate'] * 100:.1f}%")
    print(f"  D2-D7 meaningful rate        : {s['d7_meaningful_rate'] * 100:.1f}%")
    print(f"  Used dashboard quick-add     : {s['quick_add_users']}")
    print(f"  Welcome sent / clicked       : {s['welcome_sent']} / {s['welcome_clicked']}")
    print(f"  Login failed (attributed)    : {s['login_failed']}")
    print("-" * 56)
    print(f"  Median time to 1st contact   : {_fmt_duration(s['median_seconds_to_first_contact'])}")
    print(f"  Median time to activation    : {_fmt_duration(s['median_seconds_to_activation'])}")
    print(f"  Largest current stage        : {s['stalled_stage']} ({s['stalled_count']})")
    if s.get("stage_counts"):
        print("  Stage breakdown:")
        for stage, count in sorted(s["stage_counts"].items(), key=lambda x: -x[1]):
            print(f"    - {stage}: {count}")
    if s.get("friction_counts"):
        print("  Friction / churn reasons:")
        for reason, count in sorted(s["friction_counts"].items(), key=lambda x: -x[1]):
            print(f"    - {reason}: {count}")
    print("=" * 56)
    if total == 0:
        print("  (No activation events recorded yet.)")


if __name__ == "__main__":
    main()
