"""Ontology coverage measurement.

Implements the "coverage measurement" capability from the ontology-as-
knowledge-layer design this pipeline follows: rather than trusting that
every relevant check ran, the orchestrator can answer "how much of the
audit space did we actually inspect this run?" per category, and know
explicitly when a category (e.g. everything gated behind
`crawl-render-audit`) went unevaluated because a backend wasn't installed
or a budget ran out — instead of that gap silently reading as "clean."

This module never runs a check itself — it only knows, after the fact,
which *sources* (skills, optionally qualified by a precondition like
`target_queries` being supplied) were actually engaged during a run, and
cross-references that against which signals each ontology failure mode
needs to be confirmed or ruled out.
"""

from __future__ import annotations

from typing import Any

from braiaudit.ontology import Ontology, load_ontology

# Which producer (skill) emits each signal literal. A few signals are
# additionally qualified by a precondition (e.g. `target_queries` must be
# supplied) — `pipeline.engaged_skills()` emits both the plain and the
# qualified form so a signal declaring the qualified source only counts as
# covered when that precondition actually held this run.
SIGNAL_SOURCES: dict[str, str] = {
    "low_raw_text": "website-observer",
    "high_script_count": "website-observer",
    "root_container_detected": "website-observer",
    "data_src_attribute_present": "website-observer",
    "missing_schema_org": "website-observer",
    "missing_product_schema": "website-observer",
    "stale_copyright_year": "website-observer",
    "last_modified_stale_or_absent": "website-observer",
    "sitemap_lastmod_meaningless": "website-observer",
    "visible_date_contradicts_schema": "website-observer",
    "no_page_identifying_heading": "website-observer",
    "no_breadcrumb_trail": "website-observer",
    "meta_description_absent": "website-observer",
    "render_blocking_scripts": "website-observer",
    "organization_logo_missing": "website-observer",
    "brand_name_inconsistent": "website-observer",
    "contact_details_absent": "website-observer",
    "canonical_missing": "website-observer",
    "soft_404_suspected": "website-observer",
    "robots_txt_disallow": "website-observer",
    "ai_crawler_robots_disallow": "website-observer",
    "http_429_rate_limit": "website-observer",
    "anti_bot_challenge_detected": "website-observer",
    "freshness_markers_absent": "website-observer",
    "entity_sameas_missing": "website-observer",
    "schema_visual_desync": "website-observer",
    "low_main_text_ratio": "content-cleaner",
    "high_nav_footer_density": "content-cleaner",
    "zero_semantic_tags": "content-cleaner",
    "cookie_banner_detected": "content-cleaner",
    "high_z_index_overlay_present": "content-cleaner",
    "no_question_shaped_headings": "content-cleaner",
    "primary_facts_below_fold": "content-cleaner",
    "wall_of_text_structure": "content-cleaner",
    "high_internal_link_density": "query-guided-discovery",
    "partial_answer_match": "query-guided-discovery:target_queries",
    "zero_incoming_internal_links": "query-guided-discovery",
    "absent_from_sitemap": "query-guided-discovery",
    "lazy_load_confirmed": "crawl-render-audit",
    "shadow_dom_encapsulation_confirmed": "crawl-render-audit",
    "dynamic_interaction_confirmed": "crawl-render-audit",
    "onclick_div_navigation": "crawl-render-audit",
    "javascript_void_href": "crawl-render-audit",
    "render_backend_unavailable": "pipeline",
    "duplicate_content_detected": "pipeline",
}


def _mode_evaluated(signals: frozenset[str], match_mode: str, engaged: set[str]) -> bool:
    sources = {SIGNAL_SOURCES[s] for s in signals if s in SIGNAL_SOURCES}
    if not sources:
        return False
    if match_mode == "any":
        return any(source in engaged for source in sources)
    return all(source in engaged for source in sources)


def evaluable_modes_by_axis(
    engaged_skills: set[str], ontology: Ontology | None = None
) -> dict[str, list[Any]]:
    """Failure modes per axis that this run was actually able to evaluate.

    The scoring denominator: a mode gated behind a skill that never ran is
    not something the site passed, so counting it as achievable would inflate
    the score exactly where the audit is weakest.
    """
    ontology = ontology or load_ontology()
    by_axis: dict[str, list[Any]] = {}
    for mode in ontology.failure_modes.values():
        if mode.kind == "limitation":
            continue
        if not _mode_evaluated(mode.signals, mode.match_mode, engaged_skills):
            continue
        by_axis.setdefault(mode.axis, []).append(mode)
    return by_axis


def compute_coverage(engaged_skills: set[str], ontology: Ontology | None = None) -> dict[str, Any]:
    """Return per-category and overall coverage given the set of skill
    "sources" actually engaged this run (see SIGNAL_SOURCES and
    `braiaudit.pipeline.run_audit`'s `_engaged_skills` for how that set is
    built).
    """
    ontology = ontology or load_ontology()

    categories: dict[str, Any] = {}
    total_evaluated = 0
    total_modes = 0

    for category in ontology.category_order:
        modes = [fm for fm in ontology.failure_modes.values() if fm.category == category]
        evaluated_modes = [
            fm for fm in modes if _mode_evaluated(fm.signals, fm.match_mode, engaged_skills)
        ]
        not_evaluated = sorted(fm.name for fm in modes if fm not in evaluated_modes)
        categories[category] = {
            "label": ontology.categories[category]["label"],
            "total_failure_modes": len(modes),
            "evaluated_failure_modes": len(evaluated_modes),
            "coverage_pct": round(100 * len(evaluated_modes) / len(modes), 1) if modes else 100.0,
            "not_evaluated": not_evaluated,
        }
        total_evaluated += len(evaluated_modes)
        total_modes += len(modes)

    return {
        "ontology_version": ontology.version,
        "skills_engaged": sorted(engaged_skills),
        "categories": categories,
        "overall_coverage_pct": (
            round(100 * total_evaluated / total_modes, 1) if total_modes else 100.0
        ),
    }
