"""Implementation of the entrypoint skill
(skills/brand-ai-readiness-audit/SKILL.md).

Sequences website-observer -> crawl-render-audit (conditional) ->
content-cleaner -> query-guided-discovery -> failure-diagnostics ->
freshness-corroboration for a set of seed URLs, exactly per the six-step
Execution Sequence documented in that skill.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import requests

from braiaudit import (
    clean,
    discovery,
    fetch,
    ontology,
    relevance,
    render,
    report,
)
from braiaudit import (
    corroborate as corroborate_mod,
)

logger = logging.getLogger("braiaudit.pipeline")

# Signals from website-observer that justify spending render budget on a
# page — deliberately conservative so cheap pages never get rendered.
_RENDER_TRIGGER_SIGNALS = {"low_raw_text", "high_script_count", "root_container_detected"}

# Observer signals that mean "stop the pipeline for this URL right here" —
# mirrors the entrypoint skill's robots/rate-limit/anti-bot short-circuit.
_HALT_SIGNALS = {
    "robots_txt_disallow",
    "http_429_rate_limit",
    "anti_bot_challenge_detected",
    "http_error_status_blocked",
    "http_error_status_unconfirmed",
    # A 200 carrying an error stub is content we must not read as the site's.
    # Detecting it and then analysing it anyway was the whole bug: the stub
    # has no schema, no contact details and no headings, so every content and
    # identity check "failed" and the report accused the brand of seven
    # defects belonging to a page that was never theirs. The soft-404 finding
    # itself is emitted before this halt and still stands.
    "soft_404_suspected",
}

# Used when the caller supplies no target queries. Without any, answer-
# completeness scoring never runs and DISTRIBUTED_INFORMATION_FRAGMENTATION
# reads as "not evaluated" — an accurate result, but a poor default for
# someone who just typed the domain and expected a full audit. These are the
# questions a customer actually brings to an assistant; pass --query (or
# `target_queries`) to replace them with ones specific to the brand.
DEFAULT_TARGET_QUERIES = [
    "what does this product cost",
    "how do I contact support",
    "what does this company sell",
]


@dataclass
class AuditOptions:
    respect_robots: bool = True
    max_pages: int = 15
    # Rendering costs ~20-25s per page against a real site, and dominates
    # total runtime; static pages cost 1-3s. Three rendered samples is
    # ample to establish whether a site is client-side rendered,
    # interaction-gated or shadow-DOM-encapsulated — those are template
    # level properties, not per-page ones — and keeps a typical audit
    # comfortably inside the 5-minute budget rather than being truncated
    # by it. Raise with --max-render-pages when investigating one site.
    max_render_pages: int = 3
    # Link depth from the seed URLs. Depth 1 is the main nav — where a
    # brand's answerable facts live — and depth 2 reaches product/doc detail
    # pages. Depth 3+ is mostly pagination and archives: high crawl cost,
    # little AI-readiness signal, and where link-space traps live.
    max_depth: int = 2
    # Hard wall-clock ceiling for the whole crawl. Politeness costs time —
    # robots Crawl-delay is honoured, and slow or challenge-serving hosts add
    # seconds per page — so a page budget alone cannot bound runtime. The
    # crawl stops cleanly at this point and the report says it did, rather
    # than silently reporting a partial site as if it were the whole one.
    max_runtime_seconds: float = 240.0
    # Off-site corroboration reads third-party pages (Wikipedia, Wikidata,
    # the Wayback Machine) to answer the entity-resolution question that
    # no on-site check can. Runs once per audit, not per page.
    corroborate: bool = True
    user_agent: str = fetch.DEFAULT_USER_AGENT
    target_queries: list[str] = field(default_factory=list)


def run_audit(
    site: str,
    seed_urls: list[str] | None = None,
    options: AuditOptions | None = None,
) -> dict[str, Any]:
    """Run the full six-step pipeline and return the final audit report.

    This is the direct code path behind `braiaudit audit <site>` and the
    same sequence the entrypoint skill instructs Claude to orchestrate when it
    drives the skills itself via tool use instead of this CLI.
    """
    options = options or AuditOptions()
    if not options.target_queries:
        options.target_queries = list(DEFAULT_TARGET_QUERIES)
    # `site` is documented as "bare domain or full URL", so it must be
    # normalised rather than blindly prefixed — pasting a full URL used to
    # build "https://https://example.com/", which no host resolves, and the
    # audit then reported zero pages crawled without any obvious cause.
    site = fetch.site_label(site)
    seed_urls = seed_urls or [fetch.normalize_url(site)]
    session = requests.Session()
    # robots.txt / sitemap.xml are per-origin documents; fetch each once
    # per audit rather than once per page.
    origin_cache: dict[str, Any] = {}

    findings_by_url: dict[str, list[dict[str, Any]]] = {}
    pages_unreachable: list[str] = []
    # Pages that actually yielded content the readiness checks could run
    # against — reached *and* not halted. `pages_crawled` counts a blocked
    # or challenged page, because it was fetched; this does not, because
    # nothing about the site's content was observable through it. The
    # difference is what tells the scorer whether it has evidence at all.
    analysed_urls: set[str] = set()
    render_budget_remaining = options.max_render_pages
    # Exact, not inferred: a page counts as rendered only when the
    # backend actually returned available:true, so a launch failure that
    # degrades to a coverage gap is never counted as a successful render.
    pages_rendered = 0
    render_triggered = False
    incoming_links_map: dict[str, int] = {}
    sitemap_urls: list[str] = []
    content_hash_urls: dict[str, list[str]] = {}
    canonical_missing_urls: set[str] = set()
    brand_names: list[str] = []
    # Facts the observer already reads off the site's own structured data.
    # First non-empty value across the crawl wins; these are what the site
    # declares about itself, not an independent verification of the entity.
    declared_brand: str = ""
    org_legal_name: str = ""
    org_same_as: list[str] = []

    # Which "sources" (see braiaudit.coverage.SIGNAL_SOURCES) actually ran
    # this audit — feeds the final report's meta.coverage block so a gap
    # (no render backend, no target_queries supplied) reads as "not
    # evaluated" rather than silently as "clean."
    engaged: set[str] = {"pipeline"}

    # Cache so query-guided-discovery's candidate-page fetches never repeat
    # an observe+clean pass already done for a seed/rendered page this run.
    text_cache: dict[str, str | None] = {}

    def fetch_page_text(candidate_url: str) -> str | None:
        # Crawl scope is a boundary here too, not only at the frontier.
        # Discovery receives the same unfiltered link list the frontier
        # filters, so a page that redirects off-host mid-crawl can put a
        # third party's URLs in front of this callback — and without this
        # guard they were fetched, with their text then feeding
        # answer-completeness for signals about the *audited* site.
        # `allowed_hosts` resolves at call time, so the seed's apex<->www
        # expansion is honoured.
        if fetch.site_label(candidate_url) not in allowed_hosts:
            return None
        if candidate_url in text_cache:
            return text_cache[candidate_url]
        text = _observe_and_clean_text(candidate_url, options, session, origin_cache)
        text_cache[candidate_url] = text
        return text

    # Breadth-first, so every depth-1 page is audited before any budget goes
    # to depth 2. Depth-first would spend all `max_pages` descending one blog
    # subtree and never reach /pricing.
    frontier: deque[tuple[str, int]] = deque(
        (fetch.canonical_crawl_url(u), 0) for u in seed_urls
    )
    queued: set[str] = {u for u, _ in frontier}
    allowed_hosts: set[str] = {fetch.site_label(u) for u in seed_urls}

    started_at = time.monotonic()
    stopped_early = ""

    while frontier and len(findings_by_url) < options.max_pages:
        if time.monotonic() - started_at > options.max_runtime_seconds:
            stopped_early = (
                f"Crawl stopped at the {options.max_runtime_seconds:.0f}s time budget with "
                f"{len(frontier)} URL(s) still queued; findings cover the "
                f"{len(findings_by_url)} page(s) actually audited."
            )
            logger.warning(stopped_early)
            break
        url, depth = frontier.popleft()
        page_findings: list[dict[str, Any]] = []

        # --- 1. Observe ------------------------------------------------
        observed = fetch.observe(
            url, user_agent=options.user_agent, session=session, origin_cache=origin_cache
        )
        engaged.add("website-observer")
        page_findings.extend(_diagnose(url, observed))

        if depth == 0 and observed.get("final_url"):
            # A seed URL commonly redirects apex<->www (zoho.com ->
            # www.zoho.com is typical, and is exactly what typing the bare
            # domain into a browser does). Every link discovered on that
            # page lives on the resolved host, so without this the frontier
            # guard rejected the site's own link graph in full and silently
            # degraded the crawl to one page. Scoped to depth 0 only: a
            # redirect met later, mid-crawl, never expands scope — that
            # boundary is what stops the crawler wandering onto a third
            # party through an ordinary link.
            allowed_hosts.add(fetch.site_label(observed["final_url"]))

        if observed.get("http_status") is None:
            pages_unreachable.append(url)
            findings_by_url[url] = page_findings
            continue
        if _HALT_SIGNALS & set(observed["signals"]):
            findings_by_url[url] = page_findings
            continue

        # Past both guards: this page was read as the site's own content, so
        # every check below runs against real evidence.
        analysed_urls.add(url)

        for name in observed.get("brand_name_candidates") or []:
            if name not in brand_names:
                brand_names.append(name)
        if not declared_brand:
            declared_brand = observed.get("declared_brand_name") or ""
        if not org_legal_name:
            org_legal_name = observed.get("organization_legal_name") or ""
        for link in observed.get("organization_same_as") or []:
            if link not in org_same_as:
                org_same_as.append(link)
        sitemap_urls = observed.get("sitemap_urls") or sitemap_urls
        if not observed.get("canonical_tag_present"):
            canonical_missing_urls.add(url)
        best_html = observed.get("raw_html")
        pre_render_len = observed.get("raw_text_length")

        # --- 2. Render (conditional) -------------------------------------
        needs_render = bool(_RENDER_TRIGGER_SIGNALS & set(observed["signals"]))
        rendered = None
        if needs_render:
            render_triggered = True
        if needs_render and render_budget_remaining > 0:
            rendered = render.render(url, pre_render_text_length=pre_render_len)
            render_budget_remaining -= 1
            page_findings.extend(_diagnose(url, rendered))
            if rendered.get("available"):
                engaged.add("crawl-render-audit")
                best_html = rendered.get("rendered_html") or best_html
                pages_rendered += 1
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
            engaged.add("content-cleaner")
            page_findings.extend(_diagnose(url, cleaned))
            text_cache[url] = cleaned["clean_text"]
            content_hash_urls.setdefault(cleaned["content_hash"], []).append(url)

        # --- 4. Discover -----------------------------------------------------
        # Static links first so the link graph exists without a render
        # backend; rendered links (JS-injected nav) add to it when available.
        # Both sources are normalised here rather than at each producer: the
        # static extractor already canonicalises, but rendered links come
        # straight from the DOM, so `/page`, `/page/` and `/page#section`
        # would otherwise enter the frontier as three separate pages and
        # burn the budget re-auditing one.
        internal_links = sorted(
            {
                fetch.canonical_crawl_url(link)
                for link in (
                    *(observed.get("internal_links") or []),
                    *((rendered or {}).get("discovered_internal_links") or []),
                )
            }
        )
        for link in internal_links:
            incoming_links_map[link] = incoming_links_map.get(link, 0) + 1

        if depth < options.max_depth:
            for link in _crawl_order(internal_links, options.target_queries):
                # Crawl scope is a boundary, not a detail: this audit fetches
                # the brand's own site and nothing else. Producers filter too,
                # but the frontier enforces it so one leaky producer can never
                # send the crawler onto a third party.
                if link in queued or fetch.site_label(link) not in allowed_hosts:
                    continue
                queued.add(link)
                frontier.append((link, depth + 1))

        if cleaned is not None:
            engaged.add("query-guided-discovery")
            if options.target_queries:
                engaged.add("query-guided-discovery:target_queries")
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

    # --- Site-level pass: off-site corroboration -----------------------
    # One check per audit, not per page: whether a public record of this
    # brand exists is a property of the brand, not of any single URL.
    #
    # Gated on pages actually *analysed*, not merely fetched. A run whose only
    # response was an error stub or a challenge has read no brand name, no
    # legal name and no sameAs links, so "no public record corroborates this
    # brand" would be a verdict on a lookup done from the bare domain — an
    # identity accusation sourced from a page that was never the site's. This
    # is what pages_crawled_any's own docstring means by "reached nothing".
    if options.corroborate and analysed_urls:
        engaged.add("offsite-corroboration")
        bundle = corroborate_mod.corroborate(
            site, brand_names, session=session, user_agent=options.user_agent
        )
        # Site age is weak, unscored context — not a diff over snapshot
        # history, which is materially bigger scope than a single earliest-
        # snapshot lookup and was not attempted here. Attached only when the
        # entity check itself succeeded, so it never inflates an unknown
        # parse into something that looks like more evidence than it is.
        if bundle["parse_status"] == "ok":
            seen = corroborate_mod.first_seen(
                site, session=session, user_agent=options.user_agent
            )
            if seen["site_first_seen"]:
                bundle["entity_evidence"] += (
                    f" (site first archived {seen['site_first_seen']})"
                )
        seed = seed_urls[0] if seed_urls else f"https://{site}/"
        findings_by_url.setdefault(seed, []).extend(_diagnose(seed, bundle))

    # --- Site-level pass: did this audit obtain any evidence at all? ------
    # Emitted once per run, not per page: "no page could be analysed" is a
    # property of the audit, not of any single URL. Routed through the
    # ontology like every other conclusion, which is what keeps it a
    # limitation (unscored, no accusation) rather than a defect — the
    # decision lives in NO_ANALYSABLE_PAGE_EVIDENCE, not here.
    if not analysed_urls:
        # Must be the *canonical* seed: that is the key the crawl loop wrote
        # under and the spelling recorded in `pages_unreachable`. Attaching
        # to the raw seed instead would add a second dict entry that no
        # unreachable list mentions, silently turning pages_crawled 0 into 1.
        seed = (
            fetch.canonical_crawl_url(seed_urls[0]) if seed_urls else f"https://{site}/"
        )
        attempted = len(findings_by_url) or len(seed_urls)
        findings_by_url.setdefault(seed, []).extend(
            _diagnose(
                seed,
                {"signals": ["no_analysable_page_evidence"]},
                evidence_hint=(
                    f"{attempted} URL(s) attempted, "
                    f"{len(pages_unreachable)} unreachable; no response was "
                    "readable as the site's own content."
                ),
            )
        )

    pages_crawled = len({u for u in findings_by_url if u not in pages_unreachable})
    return report.assemble_report(
        site=site,
        findings_by_url=findings_by_url,
        pages_crawled=pages_crawled,
        analysable_pages=len(analysed_urls),
        pages_unreachable=pages_unreachable,
        skills_engaged=engaged,
        crawl_note=stopped_early,
        pages_rendered=pages_rendered,
        render_triggered=render_triggered,
        seed_url=seed_urls[0] if seed_urls else '',
        brand_name_candidates=brand_names,
        declared_brand_name=declared_brand,
        organization_legal_name=org_legal_name,
        organization_same_as=org_same_as,
    )


def pages_crawled_any(
    findings_by_url: dict[str, list[dict[str, Any]]], unreachable: list[str]
) -> bool:
    """Whether anything was actually reached. A crawl that reached nothing
    has no brand to corroborate, and spending third-party fetches on it
    would be noise."""
    return bool({u for u in findings_by_url if u not in unreachable})


def _crawl_order(links: list[str], target_queries: list[str]) -> list[str]:
    """Order newly-discovered links for the frontier.

    With target queries supplied, spend a limited page budget on the pages
    most likely to answer them (ranked on URL text, the only signal available
    before fetching) rather than on whatever order the nav happened to use.
    """
    if not target_queries:
        return links
    proxy = {link: discovery.url_proxy_text(link) for link in links}
    scored: dict[str, float] = dict.fromkeys(links, 0.0)
    for query in target_queries:
        for link, score in relevance.rank_candidates(query, proxy):
            scored[link] = max(scored[link], score)
    return sorted(links, key=lambda link: (-scored[link], link))


def _observe_and_clean_text(
    url: str,
    options: AuditOptions,
    session: requests.Session,
    origin_cache: dict[str, Any] | None = None,
) -> str | None:
    """Fetch+clean one candidate page for query-guided-discovery, purely
    over static HTML — discovery never spends render budget on candidates,
    only the seed pages the orchestrator already chose to render."""
    observed = fetch.observe(
        url, user_agent=options.user_agent, session=session, origin_cache=origin_cache
    )
    if observed.get("http_status") != 200 or not observed.get("raw_html"):
        return None
    return clean.clean(url, observed["raw_html"], source="raw")["clean_text"]


# Payload fields that are content, not measurements. Without this filter the
# whole page source lands in every finding's evidence string.
_BULK_FIELDS = frozenset({"raw_html", "rendered_html", "clean_text", "url", "final_url"})
_MAX_METRIC_CHARS = 400


def _diagnose(
    url: str, signal_bundle: dict[str, Any], evidence_hint: str | None = None
) -> list[dict[str, Any]]:
    metrics = {
        k: v
        for k, v in signal_bundle.items()
        if k not in _BULK_FIELDS
        and isinstance(v, int | float | str | bool)
        and not (isinstance(v, str) and len(v) > _MAX_METRIC_CHARS)
    }
    result = ontology.diagnose(
        url, signal_bundle.get("signals", []), metrics=metrics, evidence_hint=evidence_hint
    )
    return result["findings"]
