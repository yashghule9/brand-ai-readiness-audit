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

from braiaudit.schemas import data_root, repo_root, validate

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
    # Which case-study problem this failure mode belongs to, so the report
    # can be read against the four axes a brand actually cares about.
    axis: str = "visibility"
    # "defect" (something is wrong with the site) or "limitation"
    # (something this audit could not check). Limitations are reported
    # separately and never scored — see the ontology header.
    kind: str = "defect"
    # "field": observed on the real site as a visitor or crawler would meet
    # it. "lab": a proxy measured in controlled conditions that stands in for
    # a real-world property it cannot observe directly (page weight standing
    # in for load performance). Kept distinct so a proxy is never read as a
    # measurement of the thing it proxies.
    signal_type: str = "field"
    # Name of the metric that carries this mode's headline evidence. Without
    # it a finding's evidence is a dump of every metric on the page, which
    # buries the one fact that actually demonstrates the problem.
    evidence_metric: str = ""
    # What the *site owner* should change. `recovery_strategy` /
    # `recommended_tool` name the auditor's next step ("crawl-render-audit",
    # "playwright") and are useless as advice to the brand's engineer, so a
    # mode that declares `remediation` uses it for the report's suggested
    # action instead. Empty until each mode is written up.
    remediation: str = ""

    def matches(self, signal_set: set[str]) -> frozenset[str] | None:
        """Return the matched signals if this mode fires against
        `signal_set`, else None. "all" requires every signal present;
        "any" requires at least one."""
        overlap = self.signals & signal_set
        if self.match_mode == "any":
            return overlap if overlap else None
        return overlap if overlap == self.signals else None


@dataclass(frozen=True)
class Opportunity:
    """A proactive recommendation, emitted independently of any defect.

    Carries no evidence and never affects the readiness score — it is
    advice, not an observation about the site. Dropped when any of its
    `suppressed_by` failure modes fired, since that finding's own
    remediation is the more specific advice.
    """

    name: str
    axis: str
    priority: str
    rationale: str
    action: str
    suppressed_by: frozenset[str] = frozenset()


@dataclass
class Ontology:
    version: str
    categories: dict[str, dict[str, str]]
    failure_modes: dict[str, FailureMode]
    opportunities: dict[str, Opportunity] = field(default_factory=dict)
    category_order: list[str] = field(default_factory=list)

    def modes_ranked(self) -> list[FailureMode]:
        """All failure modes, ordered by severity then declaration order —
        used to give ties in freshness-corroboration a stable sort key."""
        return sorted(
            self.failure_modes.values(),
            key=lambda fm: (_SEVERITY_ORDER[fm.severity], self.category_order.index(fm.category)),
        )


def ontology_path() -> Path:
    """The failure ontology, from packaged data when installed, else the
    canonical copy under skills/ in a source checkout."""
    packaged = data_root() / "ontology.yaml"
    if packaged.is_file():
        return packaged
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
            axis=fm.get("axis", "visibility"),
            kind=fm.get("kind", "defect"),
            signal_type=fm.get("signal_type", "field"),
            evidence_metric=fm.get("evidence_metric", ""),
            remediation=(fm.get("remediation") or "").strip(),
        )
        for name, fm in raw["failure_modes"].items()
    }
    opportunities = {
        name: Opportunity(
            name=name,
            axis=op["axis"],
            priority=op["priority"],
            rationale=op["rationale"].strip(),
            action=op["action"].strip(),
            suppressed_by=frozenset(op.get("suppressed_by") or ()),
        )
        for name, op in (raw.get("opportunities") or {}).items()
    }
    unknown_suppressors = {
        mode for op in opportunities.values() for mode in op.suppressed_by
    } - set(modes)
    if unknown_suppressors:
        raise ValueError(
            f"ontology.yaml: opportunities reference undeclared failure modes: "
            f"{sorted(unknown_suppressors)}"
        )

    return Ontology(
        version=raw["ontology_version"],
        categories=categories,
        failure_modes=modes,
        opportunities=opportunities,
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
                "signal_type": fm.signal_type,
                "parse_status": (metrics.get("parse_status") or "ok"),
                "suggested_action": {
                    "summary": fm.remediation
                    or (
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
    # A mode that names its own evidence metric gets exactly that, stated
    # plainly. Everything else falls back to the full metric bundle.
    headline = metrics.get(fm.evidence_metric) if fm.evidence_metric else None
    if isinstance(headline, str) and headline.strip():
        return headline.strip().rstrip(".") + "."

    metric_bits = ", ".join(f"{k}={v}" for k, v in metrics.items())
    signal_bits = ", ".join(sorted(matched))
    if metric_bits:
        return f"Signals observed: {signal_bits}. Metrics: {metric_bits}."
    return f"Signals observed: {signal_bits}."
