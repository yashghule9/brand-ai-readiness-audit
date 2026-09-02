"""Implementation of the `freshness-corroboration` skill.

Aggregates the raw, per-URL findings emitted by `braiaudit.ontology.diagnose`
across every crawled page into the final audit report: one entry per
failure mode (not per page-hit), corroborated evidence, and the exact
floor schema defined in schemas/audit-report.schema.json. See
skills/freshness-corroboration/SKILL.md for the full spec this mirrors.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any

from braiaudit.ontology import load_ontology
from braiaudit.schemas import validate

_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3}

# compliance_and_access findings are policy statements, not statistical
# patterns — a single confirmed hit is fully corroborated regardless of
# how many pages were sampled (see SKILL.md step 2).
_NEVER_DOWNGRADED_CATEGORY = "compliance_and_access"


def assemble_report(
    site: str,
    findings_by_url: dict[str, list[dict[str, Any]]],
    pages_crawled: int,
    pages_unreachable: list[str] | None = None,
    audited_at: str | None = None,
) -> dict[str, Any]:
    ontology = load_ontology()
    pages_unreachable = pages_unreachable or []

    by_mode: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for url, findings in findings_by_url.items():
        for finding in findings:
            by_mode[finding["failure_mode"]].append((url, finding))

    merged: list[dict[str, Any]] = []
    for mode_name, occurrences in by_mode.items():
        fm = ontology.failure_modes[mode_name]
        urls = sorted({url for url, _ in occurrences})
        evidence = _corroborate_evidence(
            fm.category, urls, occurrences, pages_crawled, pages_unreachable
        )
        merged.append(
            {
                "failure_mode": mode_name,
                "category": fm.category,
                "title": fm.audit_finding_title,
                "severity": fm.severity,
                "evidence": evidence,
                "suggested_action": occurrences[0][1]["suggested_action"],
                "_affected_urls": urls,
            }
        )

    merged.sort(
        key=lambda f: (
            _SEVERITY_RANK[f["severity"]],
            ontology.category_order.index(f["category"]),
            f["title"],
        )
    )

    findings = []
    for idx, f in enumerate(merged, start=1):
        findings.append(
            {
                "id": f"F-{idx:03d}",
                "title": f["title"],
                "severity": f["severity"],
                "evidence": f["evidence"],
                "suggested_action": f["suggested_action"],
                "affected_urls": f["_affected_urls"],
                "category": f["category"],
            }
        )

    summary = {
        "total_findings": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
    }

    report = {
        "site": site,
        "audited_at": audited_at or _now_iso(),
        "summary": summary,
        "findings": findings,
    }
    validate(report, "audit-report")
    return report


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _corroborate_evidence(
    category: str,
    urls: list[str],
    occurrences: list[tuple[str, dict[str, Any]]],
    pages_crawled: int,
    pages_unreachable: list[str],
) -> str:
    n_hit = len(urls)
    if category == _NEVER_DOWNGRADED_CATEGORY:
        base = occurrences[0][1]["evidence"]
        return f"{base} (confirmed on {n_hit}/{pages_crawled} page(s) attempted)"

    if pages_crawled and n_hit == pages_crawled:
        return (
            f"Crawled {pages_crawled} page(s); {n_hit}/{pages_crawled} exhibit this failure mode."
        )

    if pages_crawled:
        example = urls[0]
        note = (
            f" ({len(pages_unreachable)} page(s) unreachable and excluded)"
            if pages_unreachable
            else ""
        )
        return (
            f"Crawled {pages_crawled} page(s); {n_hit}/{pages_crawled} exhibit this failure "
            f"mode, e.g. {example}{note}."
        )

    return "; ".join(sorted({o[1]["evidence"] for o in occurrences}))
