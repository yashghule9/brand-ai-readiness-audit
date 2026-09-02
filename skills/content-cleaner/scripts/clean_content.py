#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.clean.clean — the executable form of
this skill. Reads HTML from --file (or stdin) and prints a result matching
schemas/content-cleaner.output.schema.json to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from braiaudit.clean import clean


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="URL the HTML was fetched from (for the output record).")
    parser.add_argument("--file", help="Path to an HTML file. Reads stdin if omitted.")
    parser.add_argument("--source", choices=["raw", "rendered"], default="raw")
    args = parser.parse_args()

    html = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    result = clean(args.url, html, source=args.source)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
