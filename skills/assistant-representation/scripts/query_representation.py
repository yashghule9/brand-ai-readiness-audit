#!/usr/bin/env python
"""Thin CLI wrapper around braiaudit.assistant.evaluate_representation — the
executable form of this skill. Invoke via:

    python skills/assistant-representation/scripts/query_representation.py \
        --report report.json [--brand "Example Corp"] [--answers answers.json]

Reads a finished Phase 2 audit report (from `braiaudit audit <site>`) and
prints a Phase 3 assistant-representation report matching
schemas/assistant-representation.output.schema.json to stdout.

No assistant provider ships with this repository and no API credential is
assumed. Without `--answers`, every question returns `not_evaluated` — an
honest "not measured", never a fabricated answer.

`--answers` takes a JSON object mapping the exact question text to a real
answer already obtained elsewhere (an agent driving this skill, or a
transcript from a real API call). It replays supplied text; it never
invents any.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from braiaudit.assistant import RecordedAnswerProvider, evaluate_representation


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--report", required=True, help="Path to a finished Phase 2 audit report JSON."
    )
    parser.add_argument(
        "--brand",
        default=None,
        help="Brand name to ask about. Without one, a domain-derived label is used and "
        "marked heuristic rather than verified.",
    )
    parser.add_argument(
        "--answers",
        default=None,
        help="Path to a JSON object of {question text: answer} already obtained from a "
        "real assistant. Omit to run with no provider (every question not_evaluated).",
    )
    parser.add_argument(
        "--provider-name",
        default="recorded",
        help="Identifier recorded as the provider for --answers, e.g. the assistant used.",
    )
    parser.add_argument("--model", default=None, help="Model identifier, when known.")
    parser.add_argument(
        "--mode",
        choices=["blind", "grounded"],
        default="blind",
        help="blind (Mode A, default): no site content supplied. grounded (Mode B): a "
        "short verified-fact snippet is supplied. Results from the two are not comparable.",
    )
    args = parser.parse_args()

    report = json.loads(Path(args.report).read_text(encoding="utf-8"))

    provider = None
    if args.answers:
        answers = json.loads(Path(args.answers).read_text(encoding="utf-8"))
        provider = RecordedAnswerProvider(
            answers, provider=args.provider_name, model=args.model
        )

    result = evaluate_representation(
        report, provider=provider, brand_name=args.brand, mode=args.mode
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
