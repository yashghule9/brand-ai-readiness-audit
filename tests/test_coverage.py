"""Tests for ontology coverage measurement (braiaudit.coverage)."""

from __future__ import annotations

from braiaudit.coverage import compute_coverage
from braiaudit.ontology import load_ontology


def test_no_skills_engaged_yields_zero_coverage():
    result = compute_coverage(engaged_skills=set())
    assert result["overall_coverage_pct"] == 0.0
    for category in result["categories"].values():
        assert category["coverage_pct"] == 0.0
        assert category["not_evaluated"]


def test_all_skills_engaged_yields_full_coverage():
    all_sources = {
        "website-observer",
        "content-cleaner",
        "query-guided-discovery",
        "query-guided-discovery:target_queries",
        "crawl-render-audit",
        "pipeline",
    }
    result = compute_coverage(engaged_skills=all_sources)
    assert result["overall_coverage_pct"] == 100.0
    for category in result["categories"].values():
        assert category["coverage_pct"] == 100.0
        assert category["not_evaluated"] == []


def test_render_backend_missing_flags_render_only_modes_as_not_evaluated():
    engaged = {"website-observer", "content-cleaner", "query-guided-discovery", "pipeline"}
    result = compute_coverage(engaged_skills=engaged)
    rendering = result["categories"]["rendering_and_execution"]
    assert rendering["coverage_pct"] < 100.0
    assert "SHADOW_DOM_ENCAPSULATION" in rendering["not_evaluated"]
    assert "DYNAMIC_INTERACTION_BARRIER" in rendering["not_evaluated"]
    # APP_SHELL_EMPTY_DOM is purely website-observer signals (all static) —
    # still evaluated even with no render backend.
    assert "APP_SHELL_EMPTY_DOM" not in rendering["not_evaluated"]


def test_query_dependent_mode_requires_the_qualified_source():
    without_queries = compute_coverage(
        engaged_skills={"website-observer", "content-cleaner", "query-guided-discovery", "pipeline"}
    )
    with_queries = compute_coverage(
        engaged_skills={
            "website-observer",
            "content-cleaner",
            "query-guided-discovery",
            "query-guided-discovery:target_queries",
            "pipeline",
        }
    )
    disc_without = without_queries["categories"]["discovery_and_pathing"]
    disc_with = with_queries["categories"]["discovery_and_pathing"]
    assert "DISTRIBUTED_INFORMATION_FRAGMENTATION" in disc_without["not_evaluated"]
    assert "DISTRIBUTED_INFORMATION_FRAGMENTATION" not in disc_with["not_evaluated"]


def test_covers_every_failure_mode_in_the_ontology_exactly_once():
    ontology = load_ontology()
    result = compute_coverage(engaged_skills=set(), ontology=ontology)
    named_in_report = sum(
        cat["total_failure_modes"] for cat in result["categories"].values()
    )
    assert named_in_report == len(ontology.failure_modes)
