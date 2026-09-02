"""Command-line entry point: `braiaudit audit <site>` / `braiaudit validate <file>`."""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from braiaudit import __version__, render
from braiaudit.pipeline import AuditOptions, run_audit
from braiaudit.schemas import is_valid


def _audit(args: argparse.Namespace) -> int:
    options = AuditOptions(
        max_pages=args.max_pages,
        max_render_pages=0 if args.no_render else args.max_render_pages,
        target_queries=args.query or [],
    )
    if not render.is_available() and not args.no_render:
        print(
            "note: Playwright is not installed — render-dependent checks will be reported "
            "as a coverage gap rather than verified. Install with `pip install braiaudit[render] "
            "&& playwright install chromium` for full JS-rendering coverage.",
            file=sys.stderr,
        )

    report = run_audit(site=args.site, seed_urls=args.seed_url or None, options=options)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
        print(f"Wrote report to {args.output}", file=sys.stderr)
    else:
        print(text)
    return 1 if report["summary"]["critical"] > 0 and args.fail_on_critical else 0


def _validate(args: argparse.Namespace) -> int:
    payload: Any = json.loads(args.file.read_text(encoding="utf-8"))
    ok, error = is_valid(payload, args.schema)
    if ok:
        print(f"OK: {args.file} validates against '{args.schema}'.")
        return 0
    print(
        f"INVALID: {args.file} does not validate against '{args.schema}':\n  {error}",
        file=sys.stderr,
    )
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="braiaudit", description=__doc__)
    parser.add_argument("--version", action="version", version=f"braiaudit {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    audit_p = sub.add_parser("audit", help="Run a full Brand AI Readiness Audit against a site.")
    audit_p.add_argument("site", help="Bare domain or full URL, e.g. example.com")
    audit_p.add_argument(
        "--seed-url",
        action="append",
        help="Explicit seed URL (repeatable). Defaults to https://<site>/",
    )
    audit_p.add_argument(
        "--query", action="append", help="A target query to test discoverability of (repeatable)."
    )
    audit_p.add_argument("--max-pages", type=int, default=15)
    audit_p.add_argument("--max-render-pages", type=int, default=5)
    audit_p.add_argument(
        "--no-render", action="store_true", help="Skip headless rendering entirely."
    )
    audit_p.add_argument(
        "--fail-on-critical", action="store_true", help="Exit 1 if any critical finding is present."
    )
    audit_p.add_argument(
        "--output", type=pathlib.Path, help="Write the JSON report to this file instead of stdout."
    )
    audit_p.set_defaults(func=_audit)

    validate_p = sub.add_parser(
        "validate", help="Validate a JSON file against one of the repo's schemas."
    )
    validate_p.add_argument("file", type=pathlib.Path)
    validate_p.add_argument(
        "--schema",
        default="audit-report",
        choices=[
            "audit-report",
            "website-observer",
            "crawl-render-audit",
            "content-cleaner",
            "query-guided-discovery",
            "failure-diagnostics",
            "ontology",
            "marketplace",
        ],
    )
    validate_p.set_defaults(func=_validate)

    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
