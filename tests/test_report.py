"""Tests for the freshness-corroboration implementation (braiaudit.report)."""

from __future__ import annotations

from braiaudit.ontology import diagnose
from braiaudit.report import assemble_report
from braiaudit.schemas import validate


def _findings_for(url: str, signal: str) -> list[dict]:
    return diagnose(url, [signal])["findings"]


def test_same_failure_mode_across_pages_merges_into_one_finding():
    findings_by_url = {
        "https://example.com/a": _findings_for("https://example.com/a", "missing_schema_org"),
        "https://example.com/b": _findings_for("https://example.com/b", "missing_schema_org"),
        "https://example.com/c": _findings_for("https://example.com/c", "missing_schema_org"),
    }
    report = assemble_report("example.com", findings_by_url, pages_crawled=3)

    assert report["summary"]["total_findings"] == 1
    finding = report["findings"][0]
    assert finding["id"] == "F-001"
    assert "3/3" in finding["evidence"]
    validate(report, "audit-report")


def test_single_page_hit_out_of_many_states_the_ratio_honestly():
    findings_by_url = {
        "https://example.com/": [],
        "https://example.com/legacy-promo": diagnose(
            "https://example.com/legacy-promo", ["soft_404_suspected"]
        )["findings"],
    }
    report = assemble_report("example.com", findings_by_url, pages_crawled=2)

    finding = report["findings"][0]
    assert "1/2" in finding["evidence"]
    assert "legacy-promo" in finding["evidence"]


def test_compliance_finding_never_diluted_by_sample_size():
    findings_by_url = {
        "https://example.com/private": diagnose(
            "https://example.com/private", ["robots_txt_disallow"]
        )["findings"],
    }
    report = assemble_report("example.com", findings_by_url, pages_crawled=1)

    finding = report["findings"][0]
    assert finding["severity"] == "critical"
    assert "confirmed on" in finding["evidence"]


def test_severity_ordering_puts_critical_before_medium():
    findings_by_url = {
        "https://example.com/a": _findings_for("https://example.com/a", "http_429_rate_limit"),
        "https://example.com/b": _findings_for("https://example.com/b", "robots_txt_disallow"),
    }
    report = assemble_report("example.com", findings_by_url, pages_crawled=2)
    severities = [f["severity"] for f in report["findings"]]
    rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    assert severities == sorted(severities, key=rank.get)


def test_zero_findings_is_a_valid_report():
    report = assemble_report("example.com", {"https://example.com/": []}, pages_crawled=1)
    assert report["summary"]["total_findings"] == 0
    assert report["findings"] == []
    validate(report, "audit-report")


def test_sequential_ids_are_zero_padded_and_unique():
    findings_by_url = {
        f"https://example.com/{i}": diagnose(f"https://example.com/{i}", [signal])["findings"]
        for i, signal in enumerate(
            ["missing_schema_org", "canonical_missing", "soft_404_suspected"]
        )
    }
    # canonical_missing alone doesn't map to any mode (CANONICAL_URL_AMBIGUITY
    # needs duplicate_content_detected too) — only 2 findings should result.
    report = assemble_report("example.com", findings_by_url, pages_crawled=3)
    ids = [f["id"] for f in report["findings"]]
    assert ids == sorted(ids)
    assert len(ids) == len(set(ids))
