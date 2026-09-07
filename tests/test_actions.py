"""Tests for the report's second half: the readiness score, the per-axis
breakdown, and the prioritised suggested_actions array that mixes findings'
remediations with proactive opportunities."""

from __future__ import annotations

from braiaudit.ontology import diagnose, load_ontology
from braiaudit.report import assemble_report


def _report(findings_by_url, pages_crawled=1):
    return assemble_report(
        site="example.com",
        findings_by_url=findings_by_url,
        pages_crawled=pages_crawled,
    )


def _findings_for(signals: list[str], url: str = "https://example.com/"):
    return diagnose(url, signals)["findings"]


def test_every_failure_mode_declares_an_axis_and_owner_facing_remediation():
    """The report's actions must speak to the brand's engineer. The auditor
    vocabulary (recovery_strategy / recommended_tool) leaking into a
    suggested action is the bug this guards."""
    for mode in load_ontology().failure_modes.values():
        assert mode.axis in {"visibility", "staleness", "engagement", "identity"}
        assert mode.remediation, f"{mode.name} has no owner-facing remediation"


def test_clean_site_still_receives_proactive_actions():
    """Zero findings is a valid, good outcome — and still has to produce
    advice, per the brief's "suggestions may go beyond detected problems"."""
    report = _report({})

    assert report["summary"]["total_findings"] == 0
    assert report["summary"]["readiness_score"] == 100
    actions = report["opportunities"]
    assert actions, "a clean audit produced no advice at all"
    assert {a["axis"] for a in actions} == {
        "visibility",
        "staleness",
        "engagement",
        "identity",
    }


def test_a_finding_carries_its_own_fix_and_opportunities_stay_separate():
    """Verified problems and proactive advice must not be mixed into one
    list — "0 findings, 10 recommendations" reads as a contradiction."""
    report = _report({"https://example.com/": _findings_for(["missing_schema_org"])})

    assert len(report["findings"]) == 1
    finding = report["findings"][0]
    assert "JSON-LD" in finding["suggested_action"]["summary"]
    # medium, not high: this page has nothing transactional to mark up. A
    # page quoting prices raises MISSING_PRODUCT_SCHEMA at high instead.
    assert finding["suggested_action"]["priority"] == "medium"

    # Opportunities are advice only: none of them restates a finding's fix.
    assert report["opportunities"]
    assert all(o["id"].startswith("O-") for o in report["opportunities"])


def test_opportunity_ids_are_sequential_and_priority_ordered():
    report = _report({"https://example.com/": _findings_for(["missing_schema_org"])})
    actions = report["opportunities"]

    assert [a["id"] for a in actions] == [f"O-{i:03d}" for i in range(1, len(actions) + 1)]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    priorities = [rank[a["priority"]] for a in actions]
    assert priorities == sorted(priorities)


def test_opportunity_is_dropped_when_a_finding_already_covers_it():
    """SOFT_404_MISDIRECTION carries its own succession advice, so the
    DISCONTINUED_PRODUCT_SUCCESSION opportunity would be saying it twice."""
    clean = _report({})
    assert any(
        "301 each retired product URL" in a["summary"] for a in clean["opportunities"]
    )

    with_soft_404 = _report({"https://example.com/x": _findings_for(["soft_404_suspected"])})
    assert not any(
        "301 each retired product URL" in a["summary"]
        for a in with_soft_404["opportunities"]
    )


def test_score_penalises_by_severity_and_splits_by_axis():
    report = _report(
        {
            # critical, visibility
            "https://example.com/": _findings_for(["ai_crawler_robots_disallow"]),
            # staleness
            "https://example.com/old": _findings_for(["soft_404_suspected"]),
        },
        pages_crawled=2,
    )

    readiness = report["summary"]
    # one critical (-25) and one high (-12) → 63
    assert readiness["readiness_score"] == 63
    assert readiness["by_axis"]["visibility"]["score"] == 75
    assert readiness["by_axis"]["staleness"]["score"] == 88
    assert readiness["by_axis"]["engagement"]["score"] == 100
    assert readiness["by_axis"]["engagement"]["findings"] == 0
    # The rule has to be reproducible by hand, not a black box.
    assert "25 per critical" in readiness["score_formula"]


def test_repeated_failure_mode_is_penalised_once_not_per_page():
    """Cross-page corroboration collapses a mode to one finding, so a
    sitewide problem must not be scored ten times for ten pages."""
    per_url = {
        f"https://example.com/{i}": _findings_for(
            ["ai_crawler_robots_disallow"], f"https://example.com/{i}"
        )
        for i in range(10)
    }
    report = _report(per_url, pages_crawled=10)

    assert report["summary"]["total_findings"] == 1
    assert report["summary"]["readiness_score"] == 75


def test_score_floors_at_zero():
    findings = _findings_for(
        [
            "ai_crawler_robots_disallow",  # critical, -25
            "robots_txt_disallow",  # critical, -25
            "anti_bot_challenge_detected",  # critical, -25
            "missing_schema_org",  # medium, -5
            "soft_404_suspected",  # high, -12
            "data_src_attribute_present",  # medium, -5
            "zero_semantic_tags",  # low, -2
            "high_z_index_overlay_present",  # medium, -5
        ]
    )
    report = _report({"https://example.com/": findings})

    assert report["summary"]["critical"] == 3
    assert report["summary"]["readiness_score"] == 0  # 104 penalty, floored


def test_evidence_leads_with_the_fact_not_a_metric_dump():
    """The AI-crawler finding's whole value is naming which crawlers are
    blocked. Burying that at the end of every metric on the page makes the
    report unreadable at exactly the point it matters most."""
    findings = diagnose(
        "https://example.com/",
        ["ai_crawler_robots_disallow"],
        metrics={
            "http_status": 503,
            "json_ld_present": False,
            "blocked_ai_crawlers": (
                "robots.txt disallows 2 AI crawler(s): ClaudeBot, GPTBot, "
                "while still allowing Bingbot, Googlebot"
            ),
        },
    )["findings"]

    evidence = findings[0]["evidence"]
    assert evidence.startswith("robots.txt disallows 2 AI crawler(s)")
    assert "json_ld_present" not in evidence
    assert "Signals observed" not in evidence


def test_evidence_falls_back_to_metrics_when_no_headline_is_declared():
    findings = diagnose(
        "https://example.com/", ["missing_schema_org"], metrics={"raw_text_length": 12}
    )["findings"]
    assert "Signals observed" in findings[0]["evidence"]
    assert "raw_text_length=12" in findings[0]["evidence"]


def test_a_coverage_gap_is_a_limitation_not_a_finding():
    """RENDER_COVERAGE_GAP describes the audit, not the site. Reporting it as
    a finding told a brand it had a defect and docked its score because *we*
    lacked a browser."""
    report = _report({"https://example.com/": _findings_for(["render_backend_unavailable"])})

    assert report["summary"]["total_findings"] == 0
    assert report["findings"] == []
    # A missing tool must never cost the site points.
    assert report["summary"]["readiness_score"] == 100

    limits = report["audit_limitations"]
    assert len(limits) == 1
    assert "Render-Dependent" in limits[0]["title"]
    assert limits[0]["detail"]


def test_real_defects_still_score_alongside_a_limitation():
    report = _report(
        {
            "https://example.com/": _findings_for(
                ["render_backend_unavailable", "missing_schema_org"]
            )
        }
    )
    assert report["summary"]["total_findings"] == 1
    # Only the defect scores; the limitation does not.
    assert report["summary"]["readiness_score"] == 95
    assert len(report["audit_limitations"]) == 1


def test_confidence_reflects_sample_not_severity():
    """Severity is how bad; confidence is how sure. A one-page hit out of ten
    must not read like a site-wide defect just because it is severe."""
    everywhere = {
        f"https://example.com/{i}": _findings_for(["missing_schema_org"], f"https://example.com/{i}")
        for i in range(4)
    }
    assert _report(everywhere, pages_crawled=4)["findings"][0]["confidence"] == "high"

    one_of_ten = {"https://example.com/odd": _findings_for(["soft_404_suspected"])}
    finding = _report(one_of_ten, pages_crawled=10)["findings"][0]
    assert finding["severity"] == "high"
    assert finding["confidence"] == "low"


def test_policy_findings_are_always_high_confidence():
    """A robots rule is observed directly, not sampled — one page is proof."""
    report = _report(
        {"https://example.com/": _findings_for(["ai_crawler_robots_disallow"])},
        pages_crawled=12,
    )
    assert report["findings"][0]["confidence"] == "high"


def test_headline_states_what_was_checked_not_just_what_was_found():
    report = _report({"https://example.com/": _findings_for(["missing_schema_org"])}, 6)
    headline = report["summary"]["headline"]
    assert "6 page(s)" in headline and "example.com" in headline

    # A crawl that reached nothing must say so rather than implying a clean site.
    nothing = _report({}, pages_crawled=0)
    assert "no readiness assessment" in nothing["summary"]["headline"].lower()


def test_top_priorities_point_into_the_full_arrays_and_hide_nothing():
    report = _report(
        {"https://example.com/": _findings_for(["ai_crawler_robots_disallow"])}, 3
    )
    top = report["summary"]["top_priorities"]

    assert len(top) == 3
    assert top[0]["type"] == "finding" and top[0]["priority"] == "critical"
    refs = {f["id"] for f in report["findings"]} | {o["id"] for o in report["opportunities"]}
    assert all(t["ref"] in refs for t in top)
    # The full lists are untouched — this is a pointer, not a filter.
    assert len(report["opportunities"]) > len(top)


def test_verified_findings_outrank_advice_in_top_priorities():
    """Opportunity priorities are authored by hand and are not calibrated
    against finding severities, so a `high` piece of advice must not push an
    observed defect out of the shortlist."""
    report = _report({"https://example.com/": _findings_for(["missing_schema_org"])}, 3)
    top = report["summary"]["top_priorities"]

    assert top[0]["type"] == "finding"
    finding_positions = [i for i, t in enumerate(top) if t["type"] == "finding"]
    opportunity_positions = [i for i, t in enumerate(top) if t["type"] == "opportunity"]
    assert max(finding_positions) < min(opportunity_positions)
