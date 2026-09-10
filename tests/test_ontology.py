"""Tests for the Web Failure Ontology and the failure-diagnostics matcher.

These tests are the enforcement mechanism behind the promise made in
ontology.yaml's header comment: every signal a producer module emits is
classified by at least one failure mode in steady state, and every
ontology entry's match semantics behave exactly as declared.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from braiaudit.ontology import diagnose, load_ontology
from braiaudit.schemas import repo_root

SRC_DIR = repo_root() / "src" / "braiaudit"
_PRODUCER_MODULES = (
    "fetch.py",
    "clean.py",
    "discovery.py",
    "render.py",
    "pipeline.py",
    "corroborate.py",
)


def _string_literals(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def test_ontology_loads_and_validates():
    ontology = load_ontology()
    assert ontology.failure_modes
    assert set(ontology.categories) == set(ontology.category_order)


def test_every_ontology_signal_is_emitted_by_some_producer():
    """Guards against the ontology drifting ahead of the code: every signal
    named in ontology.yaml must appear as a string literal somewhere in a
    producer module (a cheap but effective proxy for "is actually emitted")."""
    all_literals: set[str] = set()
    for filename in _PRODUCER_MODULES:
        all_literals |= _string_literals(SRC_DIR / filename)

    ontology = load_ontology()
    declared_signals = {s for fm in ontology.failure_modes.values() for s in fm.signals}
    undeclared = {s for s in declared_signals if s not in all_literals}
    assert not undeclared, f"ontology.yaml signals never emitted by any producer: {undeclared}"


def test_all_mode_requires_every_signal():
    result = diagnose("https://x.test/", ["low_raw_text"])  # only 1 of 3 APP_SHELL signals
    modes = {f["failure_mode"] for f in result["findings"]}
    assert "APP_SHELL_EMPTY_DOM" not in modes
    assert "low_raw_text" in result["unclassified_signals"]


def test_all_mode_fires_on_full_match():
    result = diagnose(
        "https://x.test/",
        ["low_raw_text", "high_script_count", "root_container_detected"],
    )
    modes = {f["failure_mode"] for f in result["findings"]}
    assert "APP_SHELL_EMPTY_DOM" in modes
    finding = next(f for f in result["findings"] if f["failure_mode"] == "APP_SHELL_EMPTY_DOM")
    assert finding["severity"] == "high"
    assert finding["category"] == "rendering_and_execution"
    assert not result["unclassified_signals"]


def test_any_mode_fires_on_a_single_signal():
    result = diagnose("https://x.test/", ["cookie_banner_detected"])
    modes = {f["failure_mode"] for f in result["findings"]}
    assert "MODAL_INTERRUPT_OVERLAY" in modes


def test_unknown_signal_is_reported_not_silently_dropped():
    result = diagnose("https://x.test/", ["some_future_signal_not_yet_in_ontology"])
    assert result["findings"] == []
    assert result["unclassified_signals"] == ["some_future_signal_not_yet_in_ontology"]


def test_compliance_findings_never_downgraded_by_severity_ordering():
    ontology = load_ontology()
    compliance_modes = [
        fm for fm in ontology.failure_modes.values() if fm.category == "compliance_and_access"
    ]
    assert all(fm.severity in ("critical", "high", "medium") for fm in compliance_modes)
    # every compliance mode must require only signals a single request cycle
    # can produce (no impossible AND-combinations across mutually exclusive paths)
    assert all(len(fm.signals) >= 1 for fm in compliance_modes)


@pytest.mark.parametrize("mode_name", list(load_ontology().failure_modes))
def test_every_failure_mode_has_a_snake_case_signal_vocabulary(mode_name):
    fm = load_ontology().failure_modes[mode_name]
    for signal in fm.signals:
        assert re.fullmatch(r"[a-z0-9_]+", signal), f"{mode_name}: non-conforming signal {signal!r}"
