"""Implementation of the `query-guided-discovery` skill.

Scores internal links against target queries to find where an answer is
fragmented across subpages, and flags pages absent from both the crawled
link graph and the sitemap. See skills/query-guided-discovery/SKILL.md.

Orphan detection here is only as complete as the `incoming_links_map` the
caller supplies — a single seed page only "knows about" its own outgoing
links. `braiaudit.pipeline` builds a fuller map by aggregating link
discovery across every page it crawls; a standalone call to `discover()`
with just one seed's links is an honest, narrower approximation, which is
why `known_urls` is an explicit parameter rather than something this module
infers on its own.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from braiaudit import relevance
from braiaudit.schemas import validate

ANSWER_THRESHOLD = 0.3
PARTIAL_LOWER_BOUND = 0.1


def discover(
    seed_url: str,
    seed_text: str,
    target_queries: list[str] | None,
    internal_links: list[str],
    sitemap_urls: list[str],
    fetch_page_text: Callable[[str], str | None],
    max_additional_pages: int = 10,
    incoming_links_map: dict[str, int] | None = None,
    known_urls: set[str] | None = None,
) -> dict[str, Any]:
    """Run the full query-guided-discovery pass for one seed page.

    `fetch_page_text(url) -> str | None` is injected so this module never
    performs I/O itself — the caller (braiaudit.pipeline, or a test) decides
    how a candidate page's text is obtained (typically fetch.observe +
    clean.clean chained together) and returns None for unreachable pages.
    """
    target_queries = target_queries or []
    query_results: list[dict[str, Any]] = []
    all_signals: set[str] = set()

    seed_link_ratio = len(internal_links) / max(len(seed_text), 1)
    if seed_link_ratio > 0.02:
        all_signals.add("high_internal_link_density")

    for query in target_queries:
        seed_completeness = relevance.answer_completeness(query, seed_text)
        entry: dict[str, Any] = {
            "query": query,
            "seed_page_answer_completeness": seed_completeness,
            "candidate_pages_ranked": [],
            "answer_found_after_discovery": seed_completeness >= ANSWER_THRESHOLD,
            "pages_required_to_answer": [seed_url] if seed_completeness >= ANSWER_THRESHOLD else [],
            "attempted_but_unreachable": [],
        }

        if PARTIAL_LOWER_BOUND <= seed_completeness < ANSWER_THRESHOLD:
            all_signals.add("partial_answer_match")

        if seed_completeness < ANSWER_THRESHOLD and internal_links:
            link_proxy_text = {url: url_proxy_text(url) for url in internal_links}
            ranked = relevance.rank_candidates(query, link_proxy_text)
            entry["candidate_pages_ranked"] = [
                {"url": url, "relevance_score": score} for url, score in ranked
            ]

            combined_text = seed_text
            required_pages = [seed_url]
            for url, _score in ranked[:max_additional_pages]:
                page_text = fetch_page_text(url)
                if page_text is None:
                    entry["attempted_but_unreachable"].append(url)
                    continue
                combined_text = f"{combined_text}\n{page_text}"
                required_pages.append(url)
                completeness = relevance.answer_completeness(query, combined_text)
                entry["seed_page_answer_completeness"] = max(
                    entry["seed_page_answer_completeness"], seed_completeness
                )
                if completeness >= ANSWER_THRESHOLD:
                    entry["answer_found_after_discovery"] = True
                    entry["pages_required_to_answer"] = required_pages
                    break
            else:
                entry["answer_found_after_discovery"] = False
                entry["pages_required_to_answer"] = (
                    required_pages if len(required_pages) > 1 else []
                )

        query_results.append(entry)

    orphaned_pages = _find_orphans(
        internal_links=internal_links,
        sitemap_urls=sitemap_urls,
        incoming_links_map=incoming_links_map or {},
        known_urls=known_urls,
    )
    if orphaned_pages:
        all_signals.add("zero_incoming_internal_links")
        if any(not p["in_sitemap"] for p in orphaned_pages):
            all_signals.add("absent_from_sitemap")

    result = {
        "seed_url": seed_url,
        "query_results": query_results,
        "orphaned_pages": orphaned_pages,
        "signals": sorted(all_signals),
    }
    validate(result, "query-guided-discovery")
    return result


def url_proxy_text(url: str) -> str:
    """Before a candidate page is actually fetched, its URL path is the
    only relevance signal available — turn /pricing/enterprise-plan into
    'pricing enterprise plan' for the bag-of-words scorer."""
    from urllib.parse import urlparse

    path = urlparse(url).path
    return path.replace("/", " ").replace("-", " ").replace("_", " ")


def _find_orphans(
    internal_links: list[str],
    sitemap_urls: list[str],
    incoming_links_map: dict[str, int],
    known_urls: set[str] | None,
) -> list[dict[str, Any]]:
    universe = known_urls if known_urls is not None else set(internal_links) | set(sitemap_urls)
    sitemap_set = set(sitemap_urls)
    orphans = []
    for url in sorted(universe):
        incoming = incoming_links_map.get(url, 1 if url in internal_links else 0)
        in_sitemap = url in sitemap_set
        if incoming == 0 and not in_sitemap:
            orphans.append({"url": url, "incoming_links": incoming, "in_sitemap": in_sitemap})
    return orphans
