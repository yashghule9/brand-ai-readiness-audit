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

# Bumped when the report's top-level shape changes, so a consumer can tell
# which contract it is reading rather than guessing from the keys present.
# 2.0: per-axis scores became a percentage of that axis's own weighted
# evaluable modes rather than "100 minus penalties". Adding failure modes
# changes an axis's denominator, so scores are comparable only within a
# schema version — hence the stamp on every report.
SCHEMA_VERSION = "2.0"

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
    crawl_note: str = "",
    seed_url: str = "",
    pages_rendered: int = 0,
    render_triggered: bool = False,
    brand_name_candidates: list[str] | None = None,
    declared_brand_name: str = "",
    organization_legal_name: str = "",
    organization_same_as: list[str] | None = None,
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
        confidence = _confidence(fm.category, len(urls), pages_crawled)
        merged.append(
            {
                "failure_mode": mode_name,
                "category": fm.category,
                "axis": fm.axis,
                "kind": fm.kind,
                "title": fm.audit_finding_title,
                "severity": fm.severity,
                "evidence": evidence,
                "confidence": confidence,
                "signal_type": fm.signal_type,
                "parse_status": _parse_status(occurrences),
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

    # A limitation describes this audit, not the site: it belongs in its own
    # section, out of the findings array and out of the score entirely.
    limitations = [
        {
            "title": f["title"],
            # Not the corroboration sentence used for defects: "1/1 exhibit
            # this failure mode" reads as an accusation about the site, when
            # the point is that we could not look.
            "detail": (
                f"Could not be verified on {len(f['_affected_urls'])} of "
                f"{pages_crawled} page(s) crawled."
            ),
            "resolution": f["suggested_action"]["summary"],
            "affected_urls": f["_affected_urls"],
        }
        for f in merged
        if f["kind"] == "limitation"
    ]
    merged = [f for f in merged if f["kind"] != "limitation"]

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
                "axis": f["axis"],
                "confidence": f["confidence"],
                # Everything in `findings` is machine-observed. Qualitative,
                # single-sample observations never land here; they carry
                # source_type "qualitative" and live outside the score.
                "source_type": "scored",
                "signal_type": f["signal_type"],
                "parse_status": f["parse_status"],
            }
        )

    summary = {
        "total_findings": len(findings),
        "critical": sum(1 for f in findings if f["severity"] == "critical"),
        "high": sum(1 for f in findings if f["severity"] == "high"),
        "medium": sum(1 for f in findings if f["severity"] == "medium"),
    }

    evaluable = coverage_mod.evaluable_modes_by_axis(skills_engaged or set(), ontology)
    readiness = _score(findings, evaluable)
    opportunities = _opportunities(findings, ontology)
    report = {
        "schema_version": SCHEMA_VERSION,
        # `site` stays a plain domain string and `audited_at` stays top-level:
        # both are required by the audit-report floor schema. Anything richer
        # belongs in site_info rather than replacing them.
        "site": site,
        "audited_at": audited_at or _now_iso(),
        "site_info": {
            "domain": site,
            "url": seed_url or f"https://{site}/",
            # What the site declares about itself in its own markup, read by
            # website-observer. Present so a consumer can see the verified
            # surface; absent/empty means the pipeline found no such
            # declaration, never that one was inferred.
            "brand_name_candidates": brand_name_candidates or [],
            "declared_brand_name": declared_brand_name,
            "organization_legal_name": organization_legal_name,
            "organization_same_as": organization_same_as or [],
        },
        # Written by the qualitative (unscored) path only. Empty here means
        # no single-sample observation was attached to this run.
        "provenance": [],
        "summary": {
            **summary,
            "readiness_score": readiness["score"],
            "by_axis": readiness["by_axis"],
            "score_formula": readiness["formula"],
            "headline": _headline(site, findings, limitations, readiness, pages_crawled),
            # The few things to do first. Nothing is hidden by this: it points
            # into the full findings and opportunities arrays below.
            "top_priorities": _top_priorities(findings, opportunities),
        },
        "findings": findings,
        "audit_limitations": limitations,
        "opportunities": opportunities,
        "meta": {},
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
        "crawl": {
            "pages_crawled": pages_crawled,
            "pages_unreachable": pages_unreachable,
            "stopped_early": bool(crawl_note),
            # Empty unless a budget cut the crawl short — a partial crawl must
            # never read as a complete one.
            "note": crawl_note,
            # Exact counts, not inferred: a page counts as rendered only when
            # the backend returned available:true, so a launch failure that
            # degraded to a coverage gap is never counted as a success.
            "render_triggered": render_triggered,
            "pages_rendered": pages_rendered,
        },
    }

    validate(report, "audit-report")
    return report


# Score weights per finding severity. Deliberately blunt and published in
# the report itself: a score nobody can recompute by hand is a black box,
# and this one exists to make runs comparable over time, not to be precise.
_SCORE_PENALTY = {"critical": 25, "high": 12, "medium": 5, "low": 2}
_AXES = ("visibility", "staleness", "engagement", "identity")


def _score(
    findings: list[dict[str, Any]], evaluable: dict[str, list[Any]]
) -> dict[str, Any]:
    """Per-axis score as a percentage of that axis's own weighted evaluable
    modes, plus an overall score computed the same way across all axes.

    Each axis is scored strictly against itself. Axes are never normalised
    against one another: a visibility axis with sixteen modes and an identity
    axis with two are not on a common ruler, and forcing them onto one would
    make every number move whenever any mode is added anywhere.

    The denominator counts only modes this run could actually evaluate. A
    check gated behind a skill that never ran is not something the site
    passed, so including it would inflate the score exactly where the audit
    is weakest.

    Only findings score. Opportunities are advice, and limitations describe
    the audit rather than the site; neither belongs in either term.
    """
    by_axis: dict[str, Any] = {}
    total_lost = 0
    total_possible = 0

    for axis in _AXES:
        axis_findings = [f for f in findings if f["axis"] == axis]
        modes = evaluable.get(axis, [])
        possible = sum(_SCORE_PENALTY[m.severity] for m in modes)
        lost = sum(_SCORE_PENALTY[f["severity"]] for f in axis_findings)
        lost = min(lost, possible)

        total_lost += lost
        total_possible += possible

        by_axis[axis] = {
            # No evaluable modes means nothing was checked on this axis. That
            # is not a perfect score; it is an absent one.
            "score": round(100 * (1 - lost / possible)) if possible else None,
            "findings": len(axis_findings),
            "evidence_basis": {
                "modes_fired": len(axis_findings),
                "modes_possible": len(modes),
            },
        }

    return {
        "score": round(100 * (1 - total_lost / total_possible)) if total_possible else None,
        "by_axis": by_axis,
        "formula": (
            "Per axis: 100 x (1 - weight of failed modes / weight of that axis's "
            "evaluable modes), weights 25 critical / 12 high / 5 medium / 2 low. "
            "Axes are scored against themselves and never normalised against each "
            "other. null means nothing on that axis could be evaluated. Adding "
            "failure modes changes an axis's denominator, so scores are comparable "
            "only between runs with the same schema_version. Opportunities and "
            "audit limitations never affect the score."
        ),
    }


def _opportunities(findings: list[dict[str, Any]], ontology: Any) -> list[dict[str, Any]]:
    """Proactive advice, kept strictly apart from verified findings.

    These are not defects and carry no evidence: they apply whether or not
    anything was found wrong, because an audit that only lists defects
    under-serves a brand whose real problem is something it never built.
    Each finding already carries its own fix in `suggested_action`, so
    nothing here restates one — an opportunity whose ground a finding
    already covers is dropped via `suppressed_by`.
    """
    actions: list[dict[str, Any]] = []
    fired = {f["title"] for f in findings}
    fired_modes = {
        name
        for name, fm in ontology.failure_modes.items()
        if fm.audit_finding_title in fired
    }
    for opportunity in ontology.opportunities.values():
        if opportunity.suppressed_by & fired_modes:
            continue
        actions.append(
            {
                "priority": opportunity.priority,
                "axis": opportunity.axis,
                "summary": opportunity.action,
                "rationale": opportunity.rationale,
            }
        )

    actions.sort(key=lambda a: (_SEVERITY_RANK[a["priority"]], a["axis"], a["summary"]))
    for index, action in enumerate(actions, start=1):
        action["id"] = f"O-{index:03d}"
    return actions


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _headline(
    site: str,
    findings: list[dict[str, Any]],
    limitations: list[dict[str, Any]],
    readiness: dict[str, Any],
    pages_crawled: int,
) -> str:
    """One sentence a non-expert can act on, stating what was actually
    checked as well as what was found — a score with no sample size behind
    it invites more confidence than the evidence supports."""
    critical = sum(1 for f in findings if f["severity"] == "critical")

    # Name the axis carrying the most findings, not the lowest score. Axis
    # scores are percentages of different denominators — an axis with two
    # evaluable modes swings between 0 and 100 in single steps, while one
    # with twelve moves smoothly — so "lowest score wins" would keep electing
    # whichever axis happens to be coarsest rather than whichever is worst.
    # Axes that could not be evaluated at all are excluded: they have nothing
    # to say either way.
    scored_axes = [
        (axis, data)
        for axis, data in readiness["by_axis"].items()
        if data["score"] is not None and data["findings"]
    ]
    worst = (
        max(scored_axes, key=lambda kv: (kv[1]["findings"], -kv[1]["score"], kv[0]))
        if scored_axes
        else None
    )

    if not pages_crawled:
        return (
            f"No pages of {site} could be crawled, so no readiness assessment "
            "was possible. See audit_limitations."
        )
    if not findings:
        head = f"No issues were detected across {pages_crawled} page(s) of {site}."
    else:
        problem = f"{len(findings)} issue(s)"
        if critical:
            problem += f", {critical} of them critical,"
        head = f"{problem} were found across {pages_crawled} page(s) of {site}"
        if worst:
            head += (
                f", most of them on {worst[0]} "
                f"({worst[1]['findings']} of {len(findings)})"
            )
        head += "."
    if limitations:
        head += f" {len(limitations)} check(s) could not be completed."
    return head


def _top_priorities(
    findings: list[dict[str, Any]], opportunities: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """The three highest-impact items, findings before advice.

    A pointer into the full arrays, never a replacement for them: a reader
    who wants everything still has it, and a reader who wants a starting
    point no longer has to rank a dozen entries themselves.
    """
    ranked = [
        {
            "ref": f["id"],
            "priority": f["severity"],
            "summary": f["title"],
            "type": "finding",
        }
        for f in findings
    ] + [
        {
            "ref": o["id"],
            "priority": o["priority"],
            "summary": o["summary"].split(".")[0].strip() + ".",
            "type": "opportunity",
        }
        for o in opportunities
    ]
    # Verified findings before advice, regardless of authored priority.
    # Opportunity priorities are hand-set in the ontology and are not
    # calibrated against finding severities, so ranking on priority alone
    # lets speculative advice crowd out an observed defect.
    ranked.sort(key=lambda a: (a["type"] == "opportunity", _SEVERITY_RANK[a["priority"]]))
    return ranked[:3]


def _parse_status(occurrences: list[tuple[str, dict[str, Any]]]) -> str:
    """`unknown` if any contributing observation could not be parsed.

    A detector that failed to read the markup must never have its silence
    read as "the site is fine" — nor, worse, as "the signal is bad". Unknown
    propagates up and scores nothing.
    """
    statuses = {o[1].get("parse_status", "ok") for o in occurrences}
    return "unknown" if "unknown" in statuses else "ok"


def _confidence(category: str, pages_hit: int, pages_crawled: int) -> str:
    """How much weight this finding's sample supports.

    Severity says how bad the problem is; confidence says how sure we are it
    is real and site-wide. They are different questions, and collapsing them
    into one number is how a single-page fluke ends up reading like a
    site-wide defect.
    """
    if category == _NEVER_DOWNGRADED_CATEGORY:
        # A robots rule or an anti-bot challenge is a policy fact observed
        # directly, not a sample to generalise from.
        return "high"
    if not pages_crawled:
        return "low"
    ratio = pages_hit / pages_crawled
    if ratio == 1.0 and pages_crawled >= 3:
        return "high"
    if ratio >= 0.5:
        return "medium"
    return "low"


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
