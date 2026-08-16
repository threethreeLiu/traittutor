#!/usr/bin/env python3
"""Run the owner-bound proactive review-reminder scheduler."""

from __future__ import annotations

import argparse
import json
import signal
import time

from traittutor.tutor_persona.reminder_worker import dispatch_tutor_reminders_once


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-seconds", type=float, default=60.0)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        results = dispatch_tutor_reminders_once()
        print(
            json.dumps(
                {
                    "owners": len(results),
                    "queued": sum(item.queued for item in results),
                    "delivered": sum(item.delivered for item in results),
                    "failed_owners": sum(item.failed for item in results),
                },
                sort_keys=True,
            ),
            flush=True,
        )
        if args.once:
            return 1 if any(item.failed for item in results) else 0
        time.sleep(args.poll_seconds)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
