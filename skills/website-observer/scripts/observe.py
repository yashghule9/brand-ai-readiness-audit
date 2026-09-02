#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.fetch.observe — the executable form of
this skill. Invoke via: `python skills/website-observer/scripts/observe.py <url>`

Prints a JSON object matching schemas/website-observer.output.schema.json
to stdout. Requires `pip install -e .` from the repository root first (see
README.md Quickstart) so the `braiaudit` package is importable.
"""

from __future__ import annotations

import argparse
import json
import sys

from braiaudit.fetch import DEFAULT_USER_AGENT, observe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Bare domain or full URL, e.g. example.com")
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)
    parser.add_argument("--timeout-ms", type=int, default=10000)
    args = parser.parse_args()

    result = observe(args.url, user_agent=args.user_agent, timeout_ms=args.timeout_ms)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
