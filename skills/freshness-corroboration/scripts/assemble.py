#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.report.assemble_report — the executable
form of this skill. Reads a JSON request from --file (or stdin) shaped like:

    {
      "site": "example.com",
      "findings_by_url": {"https://example.com/": [ ...finding dicts... ]},
      "pages_crawled": 12,
      "pages_unreachable": []
    }

and prints the final report matching schemas/audit-report.schema.json to
stdout. This is the last step of the pipeline — its output is the audit's
deliverable.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from braiaudit.report import assemble_report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Path to a JSON request file. Reads stdin if omitted.")
    args = parser.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    request = json.loads(raw)

    report = assemble_report(
        site=request["site"],
        findings_by_url=request["findings_by_url"],
        pages_crawled=request["pages_crawled"],
        pages_unreachable=request.get("pages_unreachable"),
        audited_at=request.get("audited_at"),
    )
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
