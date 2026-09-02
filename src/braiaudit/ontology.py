"""Implementation of the `failure-diagnostics` skill.

Loads the Web Failure Ontology (skills/failure-diagnostics/references/ontology.yaml)
and maps raw signal bundles emitted by the other skills onto named,
severity-scored findings, exactly per the algorithm documented in
skills/failure-diagnostics/SKILL.md.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from braiaudit.schemas import repo_root, validate

_SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class FailureMode:
    name: str
    category: str
    severity: str
    signals: frozenset[str]
    match_mode: str  # "all" or "any"
    description: str
    recovery_strategy: str
    recommended_tool: str
    audit_finding_title: str

    def matches(self, signal_set: set[str]) -> frozenset[str] | None:
        """Return the matched signals if this mode fires against
        `signal_set`, else None. "all" requires every signal present;
        "any" requires at least one."""
        overlap = self.signals & signal_set
        if self.match_mode == "any":
            return overlap if overlap else None
        return overlap if overlap == self.signals else None


@dataclass
class Ontology:
    version: str
    categories: dict[str, dict[str, str]]
    failure_modes: dict[str, FailureMode]
    category_order: list[str] = field(default_factory=list)

    def modes_ranked(self) -> list[FailureMode]:
        """All failure modes, ordered by severity then declaration order —
        used to give ties in freshness-corroboration a stable sort key."""
        return sorted(
            self.failure_modes.values(),
            key=lambda fm: (_SEVERITY_ORDER[fm.severity], self.category_order.index(fm.category)),
        )


def ontology_path() -> Path:
    return repo_root() / "skills" / "failure-diagnostics" / "references" / "ontology.yaml"


@functools.lru_cache(maxsize=1)
def load_ontology() -> Ontology:
    path = ontology_path()
    with path.open(encoding="utf-8") as fh:
        raw = yaml.safe_load(fh)

    validate(raw, "ontology")

    categories = raw["categories"]
    unknown_categories = {
        fm["category"] for fm in raw["failure_modes"].values()
    } - set(categories)
    if unknown_categories:
        raise ValueError(
            f"ontology.yaml: failure_modes reference undeclared categories: "
            f"{sorted(unknown_categories)}"
        )

    modes = {
        name: FailureMode(
            name=name,
            category=fm["category"],
            severity=fm["severity"],
            signals=frozenset(fm["signals"]),
            match_mode=fm.get("match_mode", "all"),
            description=fm["description"].strip(),
            recovery_strategy=fm["recovery_strategy"],
            recommended_tool=fm["recommended_tool"],
            audit_finding_title=fm["audit_finding_title"],
        )
        for name, fm in raw["failure_modes"].items()
    }
    return Ontology(
        version=raw["ontology_version"],
        categories=categories,
        failure_modes=modes,
        category_order=list(categories.keys()),
    )


def _priority_for(severity: str) -> str:
    return severity


def diagnose(
    url: str,
    signals: list[str],
    metrics: dict[str, Any] | None = None,
    evidence_hint: str | None = None,
) -> dict[str, Any]:
    """Classify one page's signal bundle into findings.

    Mirrors skills/failure-diagnostics/SKILL.md step-by-step: a failure
    mode fires only when its full match criterion is met (see
    FailureMode.matches — "all" signals present, or "any" one of them,
    per the mode's declared match_mode), never on a weaker partial
    overlap. Returns a dict matching
    schemas/failure-diagnostics.output.schema.json.
    """
    ontology = load_ontology()
    metrics = metrics or {}
    signal_set = set(signals)
    matched_signal_names: set[str] = set()

    findings: list[dict[str, Any]] = []
    for fm in ontology.modes_ranked():
        overlap = fm.matches(signal_set)
        if overlap is None:
            continue
        matched_signal_names |= overlap

        evidence = evidence_hint or _default_evidence(fm, overlap, metrics)
        findings.append(
            {
                "url": url,
                "failure_mode": fm.name,
                "category": fm.category,
                "title": fm.audit_finding_title,
                "severity": fm.severity,
                "evidence": evidence,
                "matched_signals": sorted(overlap),
                "suggested_action": {
                    "summary": (
                        f"{fm.description} Recommended remediation path: "
                        f"{fm.recovery_strategy} (tool: {fm.recommended_tool})."
                    ),
                    "priority": _priority_for(fm.severity),
                },
            }
        )

    result = {
        "findings": findings,
        "unclassified_signals": sorted(signal_set - matched_signal_names),
    }
    validate(result, "failure-diagnostics")
    return result


def _default_evidence(fm: FailureMode, matched: set[str], metrics: dict[str, Any]) -> str:
    metric_bits = ", ".join(f"{k}={v}" for k, v in metrics.items())
    signal_bits = ", ".join(sorted(matched))
    if metric_bits:
        return f"Signals observed: {signal_bits}. Metrics: {metric_bits}."
    return f"Signals observed: {signal_bits}."
