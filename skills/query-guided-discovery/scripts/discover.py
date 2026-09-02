#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.discovery.discover — the executable
form of this skill. Reads a JSON request from --file (or stdin) shaped like:

    {
      "seed_url": "https://example.com/",
      "seed_text": "...",
      "target_queries": ["what does this product cost"],
      "internal_links": ["https://example.com/pricing"],
      "sitemap_urls": []
    }

Candidate pages are fetched live over plain HTTP (never rendered — see
skills/query-guided-discovery/SKILL.md's preconditions) via braiaudit.fetch
+ braiaudit.clean, and the result is printed matching
schemas/query-guided-discovery.output.schema.json.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import requests

from braiaudit.clean import clean
from braiaudit.discovery import discover
from braiaudit.fetch import DEFAULT_USER_AGENT, observe


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", help="Path to a JSON request file. Reads stdin if omitted.")
    parser.add_argument("--max-additional-pages", type=int, default=10)
    args = parser.parse_args()

    raw = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()
    request = json.loads(raw)
    session = requests.Session()

    def fetch_page_text(url: str) -> str | None:
        observed = observe(url, user_agent=DEFAULT_USER_AGENT, session=session)
        if observed.get("http_status") != 200 or not observed.get("raw_html"):
            return None
        return clean(url, observed["raw_html"], source="raw")["clean_text"]

    result = discover(
        seed_url=request["seed_url"],
        seed_text=request["seed_text"],
        target_queries=request.get("target_queries"),
        internal_links=request.get("internal_links", []),
        sitemap_urls=request.get("sitemap_urls", []),
        fetch_page_text=fetch_page_text,
        max_additional_pages=args.max_additional_pages,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
