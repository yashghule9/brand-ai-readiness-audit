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

from braiaudit import coverage as coverage_mod
from braiaudit import meta as meta_mod
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
    skills_engaged: set[str] | None = None,
) -> dict[str, Any]:
    """Assemble, corroborate, and validate the final audit report.

    `skills_engaged` (see `braiaudit.coverage.SIGNAL_SOURCES` and
    `braiaudit.pipeline._engaged_skills`) drives the `meta.coverage` block —
    omit it only for a standalone/test call where coverage reporting isn't
    needed; the report still validates against the floor schema either way,
    since `meta` is an additive, non-required field.
    """
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

    # --- Meta-analysis stage: "are our conclusions about what's wrong with
    # the site actually correct, consistent, non-duplicated, and
    # well-supported?" — a different question than the findings themselves
    # answer. See braiaudit.meta and braiaudit.coverage for what each half
    # checks; both are additive (schema-optional) and never change a
    # finding's content, only report on the findings as a whole.
    report["meta"] = {
        "coverage": coverage_mod.compute_coverage(skills_engaged or set(), ontology=ontology),
        "validation": meta_mod.validate_report(report),
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
