#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.render.render — the executable form of
this skill. Requires the optional render extra:

    pip install -e ".[render]" && playwright install chromium

Without it, prints `{"available": false, ...}` rather than failing — see
skills/crawl-render-audit/SKILL.md's Error Handling section.
"""

from __future__ import annotations

import argparse
import json
import sys

from braiaudit.render import render


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url")
    parser.add_argument("--pre-render-text-length", type=int, default=None)
    parser.add_argument("--max-wait-ms", type=int, default=8000)
    parser.add_argument("--no-scroll", action="store_true")
    parser.add_argument("--no-interactions", action="store_true")
    parser.add_argument("--no-shadow-dom", action="store_true")
    args = parser.parse_args()

    result = render(
        args.url,
        pre_render_text_length=args.pre_render_text_length,
        max_wait_ms=args.max_wait_ms,
        drive_scroll=not args.no_scroll,
        drive_interactions=not args.no_interactions,
        traverse_shadow_dom=not args.no_shadow_dom,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
