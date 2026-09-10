"""Smoke tests for the `braiaudit` CLI's argument parsing and validate subcommand."""

from __future__ import annotations

import json

from braiaudit.cli import build_parser


def test_audit_subcommand_parses_defaults():
    parser = build_parser()
    args = parser.parse_args(["audit", "example.com"])
    assert args.site == "example.com"
    assert args.max_pages == 15
    # Rendering dominates runtime (~20-25s/page), so the default is kept low
    # enough that a typical audit finishes inside the 5-minute budget.
    assert args.max_render_pages == 3
    assert args.max_depth == 2
    assert args.max_runtime == 240.0
    assert args.no_render is False


def test_audit_subcommand_parses_repeated_options():
    parser = build_parser()
    args = parser.parse_args(
        [
            "audit",
            "example.com",
            "--query",
            "what does this cost",
            "--query",
            "how do I contact support",
            "--max-pages",
            "5",
            "--no-render",
        ]
    )
    assert args.query == ["what does this cost", "how do I contact support"]
    assert args.max_pages == 5
    assert args.no_render is True


def test_validate_subcommand_accepts_a_conformant_report(tmp_path):
    report = {
        "site": "example.com",
        "audited_at": "2026-09-02T00:00:00Z",
        "summary": {"total_findings": 0, "critical": 0, "high": 0, "medium": 0},
        "findings": [],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["validate", str(report_path)])
    assert args.func(args) == 0


def test_validate_subcommand_rejects_a_malformed_report(tmp_path):
    report_path = tmp_path / "bad.json"
    report_path.write_text(json.dumps({"site": "example.com"}), encoding="utf-8")

    parser = build_parser()
    args = parser.parse_args(["validate", str(report_path)])
    assert args.func(args) == 1
