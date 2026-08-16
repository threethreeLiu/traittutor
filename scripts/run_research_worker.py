#!/usr/bin/env python3
"""Run the cross-owner durable Research Workspace dispatcher."""

from __future__ import annotations

import argparse
import json
import signal
import time

from traittutor.research_workspace.runtime import dispatch_research_once


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    parser.add_argument("--limit-per-owner", type=int, default=10)
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.poll_seconds <= 0:
        raise SystemExit("--poll-seconds must be positive")
    stopping = False

    def stop(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    while not stopping:
        results = dispatch_research_once(limit_per_owner=args.limit_per_owner)
        print(
            json.dumps(
                {
                    "owners": len(results),
                    "claimed": sum(item.claimed for item in results),
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
