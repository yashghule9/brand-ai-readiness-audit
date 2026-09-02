"""Implementation of the root `SKILL.md` orchestrator.

Sequences website-observer -> crawl-render-audit (conditional) ->
content-cleaner -> query-guided-discovery -> failure-diagnostics ->
freshness-corroboration for a set of seed URLs, exactly per the six-step
Execution Sequence documented in the repository's root SKILL.md.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import requests

from braiaudit import clean, discovery, fetch, ontology, render, report

logger = logging.getLogger("braiaudit.pipeline")

# Signals from website-observer that justify spending render budget on a
# page — deliberately conservative so cheap pages never get rendered.
_RENDER_TRIGGER_SIGNALS = {"low_raw_text", "high_script_count", "root_container_detected"}

# Observer signals that mean "stop the pipeline for this URL right here" —
# mirrors the root SKILL.md's robots/rate-limit/anti-bot short-circuit.
_HALT_SIGNALS = {"robots_txt_disallow", "http_429_rate_limit", "anti_bot_challenge_detected"}


@dataclass
class AuditOptions:
    respect_robots: bool = True
    max_pages: int = 15
    max_render_pages: int = 5
    user_agent: str = fetch.DEFAULT_USER_AGENT
    target_queries: list[str] = field(default_factory=list)


def run_audit(
    site: str,
    seed_urls: list[str] | None = None,
    options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Run the full six-step pipeline and return the final audit report.

    This is the direct code path behind `braiaudit audit <site>` and the
    same sequence the root SKILL.md instructs Claude to orchestrate when it
    drives the skills itself via tool use instead of this CLI.
    """
    options = options or AuditOptions()
    seed_urls = seed_urls or [f"https://{site}/"]
    session = requests.Session()

    findings_by_url: dict[str, list[dict[str, Any]]] = {}
    pages_unreachable: list[str] = []
    render_budget_remaining = options.max_render_pages
    incoming_links_map: dict[str, int] = {}
    sitemap_urls: list[str] = []
    content_hash_urls: dict[str, list[str]] = {}
    canonical_missing_urls: set[str] = set()

    # Cache so query-guided-discovery's candidate-page fetches never repeat
    # an observe+clean pass already done for a seed/rendered page this run.
    text_cache: dict[str, str | None] = {}

    def fetch_page_text(candidate_url: str) -> str | None:
        if candidate_url in text_cache:
            return text_cache[candidate_url]
        text = _observe_and_clean_text(candidate_url, options, session)
        text_cache[candidate_url] = text
        return text

    for url in list(seed_urls)[: options.max_pages]:
        page_findings: list[dict[str, Any]] = []

        # --- 1. Observe ------------------------------------------------
        observed = fetch.observe(url, user_agent=options.user_agent, session=session)
        page_findings.extend(_diagnose(url, observed))

        if observed.get("http_status") is None:
            pages_unreachable.append(url)
            findings_by_url[url] = page_findings
            continue
        if _HALT_SIGNALS & set(observed["signals"]):
            findings_by_url[url] = page_findings
            continue

        sitemap_urls = observed.get("sitemap_urls") or sitemap_urls
        if not observed.get("canonical_tag_present"):
            canonical_missing_urls.add(url)
        best_html = observed.get("raw_html")
        pre_render_len = observed.get("raw_text_length")

        # --- 2. Render (conditional) -------------------------------------
        needs_render = bool(_RENDER_TRIGGER_SIGNALS & set(observed["signals"]))
        rendered = None
        if needs_render and render_budget_remaining > 0:
            rendered = render.render(url, pre_render_text_length=pre_render_len)
            render_budget_remaining -= 1
            page_findings.extend(_diagnose(url, rendered))
            if rendered.get("available"):
                best_html = rendered.get("rendered_html") or best_html
        elif needs_render:
            page_findings.extend(
                _diagnose(
                    url,
                    {"signals": ["render_backend_unavailable"]},
                    evidence_hint=(
                        "Page appears client-side-rendered but the render budget "
                        f"({options.max_render_pages} page(s)) was already exhausted "
                        "for this audit."
                    ),
                )
            )

        # --- 3. Clean ------------------------------------------------------
        cleaned = None
        if best_html:
            rendered_available = bool(rendered and rendered.get("available"))
            cleaned = clean.clean(
                url, best_html, source="rendered" if rendered_available else "raw"
            )
            page_findings.extend(_diagnose(url, cleaned))
            text_cache[url] = cleaned["clean_text"]
            content_hash_urls.setdefault(cleaned["content_hash"], []).append(url)

        # --- 4. Discover -----------------------------------------------------
        internal_links = (rendered or {}).get("discovered_internal_links") or []
        for link in internal_links:
            incoming_links_map[link] = incoming_links_map.get(link, 0) + 1

        if cleaned is not None:
            discovered = discovery.discover(
                seed_url=url,
                seed_text=cleaned["clean_text"],
                target_queries=options.target_queries,
                internal_links=internal_links,
                sitemap_urls=sitemap_urls,
                fetch_page_text=fetch_page_text,
                incoming_links_map=incoming_links_map,
            )
            page_findings.extend(_diagnose(url, discovered))

        findings_by_url[url] = page_findings

    # --- Cross-page pass: duplicate content without a disambiguating
    # canonical tag (CANONICAL_URL_AMBIGUITY) can only be detected once
    # every page's content hash is known, so it runs after the main loop.
    for hash_urls in content_hash_urls.values():
        if len(hash_urls) < 2:
            continue
        for url in hash_urls:
            if url not in canonical_missing_urls:
                continue
            findings_by_url.setdefault(url, []).extend(
                _diagnose(
                    url,
                    {"signals": ["duplicate_content_detected", "canonical_missing"]},
                    evidence_hint=(
                        f"Content identical to {len(hash_urls) - 1} other crawled URL(s) "
                        f"({', '.join(u for u in hash_urls if u != url)}), and no "
                        "<link rel=\"canonical\"> disambiguates them."
                    ),
                )
            )

    pages_crawled = len({u for u in findings_by_url if u not in pages_unreachable})
    return report.assemble_report(
        site=site,
        findings_by_url=findings_by_url,
        pages_crawled=pages_crawled,
        pages_unreachable=pages_unreachable,
    )


def _observe_and_clean_text(
    url: str, options: AuditOptions, session: requests.Session
) -> str | None:
    """Fetch+clean one candidate page for query-guided-discovery, purely
    over static HTML — discovery never spends render budget on candidates,
    only the seed pages the orchestrator already chose to render."""
    observed = fetch.observe(url, user_agent=options.user_agent, session=session)
    if observed.get("http_status") != 200 or not observed.get("raw_html"):
        return None
    return clean.clean(url, observed["raw_html"], source="raw")["clean_text"]


def _diagnose(
    url: str, signal_bundle: dict[str, Any], evidence_hint: str | None = None
) -> list[dict[str, Any]]:
    metrics = {k: v for k, v in signal_bundle.items() if isinstance(v, int | float | str | bool)}
    result = ontology.diagnose(
        url, signal_bundle.get("signals", []), metrics=metrics, evidence_hint=evidence_hint
    )
    return result["findings"]
