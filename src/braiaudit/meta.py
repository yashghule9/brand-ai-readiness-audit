"""The meta-analysis / validation stage of freshness-corroboration.

Sits between "raw findings assembled" and "report handed to the user" and
asks a different question than every other module in this pipeline:

    Normal analysis:  "What's wrong with the website?"
    Meta-analysis:    "Are our conclusions about what's wrong with the
                       website actually correct, consistent, non-duplicated,
                       and well-supported?"

This module never changes what was found — it only checks the *shape* of
the conclusions (structural sanity: no duplicate/out-of-order IDs, summary
counts that actually match the findings array, every finding backed by
non-empty evidence) and surfaces low-confidence correlations (multiple
distinct findings affecting the exact same set of URLs, which may share a
root cause) as informational notes for a human to judge — it does not
attempt to auto-merge them, since that requires judgment this deterministic
pass can't responsibly make on its own.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def validate_report(report: dict[str, Any]) -> dict[str, Any]:
    findings = report.get("findings", [])
    checks: list[dict[str, Any]] = []

    titles = [f["title"] for f in findings]
    checks.append(
        _check(
            "no_duplicate_titles",
            len(titles) == len(set(titles)),
            "every finding is for a distinct concept, not a re-report of another",
        )
    )

    ids = [f["id"] for f in findings]
    checks.append(
        _check("no_duplicate_ids", len(ids) == len(set(ids)), "every finding has a unique id")
    )

    expected_ids = [f"F-{i:03d}" for i in range(1, len(findings) + 1)]
    checks.append(
        _check(
            "sequential_ids",
            ids == expected_ids,
            "ids are sequential F-001, F-002, ... with no gaps",
        )
    )

    checks.append(
        _check(
            "evidence_non_empty",
            all(bool((f.get("evidence") or "").strip()) for f in findings),
            "every finding cites concrete, non-empty evidence",
        )
    )

    severities = [_SEVERITY_RANK.get(f["severity"], 99) for f in findings]
    checks.append(
        _check(
            "severity_sorted",
            severities == sorted(severities),
            "findings are ordered critical -> high -> medium -> low",
        )
    )

    summary = report.get("summary") or {}
    recomputed = {
        "total_findings": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
    }
    checks.append(
        _check(
            "summary_matches_findings",
            all(summary.get(k) == v for k, v in recomputed.items()),
            "the summary block's counts are recomputable from the findings array",
        )
    )

    opportunities = report.get("opportunities", [])
    if opportunities:
        ids = [o["id"] for o in opportunities]
        checks.append(
            _check(
                "sequential_opportunity_ids",
                ids == [f"O-{i:03d}" for i in range(1, len(opportunities) + 1)],
                "opportunity ids are sequential O-001, O-002, ... with no gaps",
            )
        )

    checks.append(
        _check(
            "every_finding_has_an_action",
            all((f.get("suggested_action") or {}).get("summary") for f in findings),
            "no problem is reported without telling the reader what to do about it",
        )
    )

    return {
        "passed": all(c["passed"] for c in checks),
        "checks": checks,
        "notes": _correlate_findings(findings),
    }


def _check(name: str, passed: bool, means: str) -> dict[str, Any]:
    return {"name": name, "passed": passed, "means": means}


def _correlate_findings(findings: list[dict[str, Any]]) -> list[str]:
    """Flag (never merge) findings that affect the exact same set of URLs —
    a human-legible hint that they might share a root cause, per the
    "root-cause" step of the meta-analysis design this mirrors. Judging
    whether they're actually related is left to a person or to Claude
    reading the report, not asserted here."""
    by_urls: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for finding in findings:
        urls = tuple(sorted(finding.get("affected_urls") or []))
        if urls:
            by_urls[urls].append(finding["title"])

    notes = []
    for urls, titles in sorted(by_urls.items()):
        if len(titles) > 1:
            url_list = ", ".join(urls) if len(urls) <= 3 else f"{len(urls)} URLs"
            notes.append(
                f"{len(titles)} findings affect exactly the same page(s) ({url_list}) and may "
                f"share a root cause rather than being independent problems: {'; '.join(titles)}."
            )
    return notes
