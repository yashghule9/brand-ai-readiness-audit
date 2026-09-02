#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.ontology.diagnose — the executable form
of this skill. Reads a signal bundle as JSON from stdin (or --file) shaped
like:

    {"url": "https://example.com/", "signals": ["low_raw_text", ...], "metrics": {...}}

and prints findings matching schemas/failure-diagnostics.output.schema.json
to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from braiaudit.ontology import diagnose


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file", help="Path to a JSON signal-bundle file. Reads stdin if omitted."
    )
    args = parser.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    bundle = json.loads(raw)

    result = diagnose(
        url=bundle["url"],
        signals=bundle.get("signals", []),
        metrics=bundle.get("metrics"),
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
