"""Tests for the meta-analysis / validation stage (braiaudit.meta)."""

from __future__ import annotations

from braiaudit.meta import validate_report


def _base_report(findings):
    return {
        "site": "example.com",
        "audited_at": "2026-09-03T00:00:00Z",
        "summary": {
            "total_findings": len(findings),
            "critical": sum(1 for f in findings if f["severity"] == "critical"),
            "high": sum(1 for f in findings if f["severity"] == "high"),
            "medium": sum(1 for f in findings if f["severity"] == "medium"),
        },
        "findings": findings,
    }


def _finding(id_, title, severity, evidence="some evidence", urls=None):
    return {
        "id": id_,
        "title": title,
        "severity": severity,
        "evidence": evidence,
        "suggested_action": {"summary": "do something", "priority": severity},
        "affected_urls": urls or [],
    }


def test_a_well_formed_report_passes_every_check():
    findings = [
        _finding("F-001", "Robots.txt Rules Block AI Scrapers", "critical"),
        _finding("F-002", "No Schema.org / JSON-LD Structured Data Available", "high"),
    ]
    result = validate_report(_base_report(findings))
    assert result["passed"] is True
    assert all(c["passed"] for c in result["checks"])


def test_duplicate_ids_fail_the_check():
    findings = [
        _finding("F-001", "Title A", "high"),
        _finding("F-001", "Title B", "high"),
    ]
    result = validate_report(_base_report(findings))
    assert result["passed"] is False
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "no_duplicate_ids" in failed


def test_out_of_order_severity_fails_the_check():
    findings = [
        _finding("F-001", "Title A", "medium"),
        _finding("F-002", "Title B", "critical"),
    ]
    result = validate_report(_base_report(findings))
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "severity_sorted" in failed


def test_empty_evidence_fails_the_check():
    findings = [_finding("F-001", "Title A", "high", evidence="   ")]
    result = validate_report(_base_report(findings))
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "evidence_non_empty" in failed


def test_summary_mismatch_fails_the_check():
    report = _base_report([_finding("F-001", "Title A", "high")])
    report["summary"]["high"] = 0  # tampered
    result = validate_report(report)
    failed = {c["name"] for c in result["checks"] if not c["passed"]}
    assert "summary_matches_findings" in failed


def test_findings_sharing_urls_are_noted_but_not_merged():
    findings = [
        _finding("F-001", "Title A", "high", urls=["https://example.com/"]),
        _finding("F-002", "Title B", "medium", urls=["https://example.com/"]),
    ]
    result = validate_report(_base_report(findings))
    assert result["passed"] is True  # a note, not a failure
    assert len(result["notes"]) == 1
    assert "Title A" in result["notes"][0] and "Title B" in result["notes"][0]


def test_findings_with_no_urls_are_never_correlated():
    findings = [_finding("F-001", "Title A", "high"), _finding("F-002", "Title B", "medium")]
    result = validate_report(_base_report(findings))
    assert result["notes"] == []


def test_zero_findings_report_passes_cleanly():
    result = validate_report(_base_report([]))
    assert result["passed"] is True
    assert result["notes"] == []
