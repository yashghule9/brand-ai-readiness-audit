from __future__ import annotations

from braiaudit.discovery import discover


def test_seed_page_already_answers_query_needs_no_further_crawl():
    result = discover(
        seed_url="https://example.com/pricing",
        seed_text="Our enterprise plan costs $49 per seat per month, billed annually.",
        target_queries=["what does this product cost"],
        internal_links=[],
        sitemap_urls=["https://example.com/pricing"],
        fetch_page_text=lambda _url: None,
    )
    entry = result["query_results"][0]
    assert entry["answer_found_after_discovery"] is True
    assert entry["pages_required_to_answer"] == ["https://example.com/pricing"]


def test_fragmented_answer_is_found_via_a_candidate_link():
    def fetch_text(url: str) -> str | None:
        return {
            "https://example.com/pricing": "Our enterprise plan costs $49 per seat per month.",
        }.get(url)

    result = discover(
        seed_url="https://example.com/",
        seed_text="Welcome to Acme. We build tools for modern teams.",
        target_queries=["what does this product cost"],
        internal_links=["https://example.com/pricing", "https://example.com/careers"],
        sitemap_urls=[],
        fetch_page_text=fetch_text,
    )
    entry = result["query_results"][0]
    assert entry["answer_found_after_discovery"] is True
    assert "https://example.com/pricing" in entry["pages_required_to_answer"]


def test_unreachable_candidate_is_not_counted_as_answering():
    result = discover(
        seed_url="https://example.com/",
        seed_text="Welcome to Acme.",
        target_queries=["what does this product cost"],
        internal_links=["https://example.com/pricing"],
        sitemap_urls=[],
        fetch_page_text=lambda _url: None,
    )
    entry = result["query_results"][0]
    assert entry["answer_found_after_discovery"] is False
    assert "https://example.com/pricing" in entry["attempted_but_unreachable"]


def test_orphan_detection_flags_pages_with_no_incoming_link_and_no_sitemap_entry():
    result = discover(
        seed_url="https://example.com/",
        seed_text="Welcome to Acme.",
        target_queries=None,
        internal_links=["https://example.com/pricing"],
        sitemap_urls=["https://example.com/pricing"],
        fetch_page_text=lambda _url: None,
        known_urls={
            "https://example.com/pricing",
            "https://example.com/legacy-promo",
        },
    )
    orphan_urls = {p["url"] for p in result["orphaned_pages"]}
    assert "https://example.com/legacy-promo" in orphan_urls
    assert "https://example.com/pricing" not in orphan_urls
    assert "zero_incoming_internal_links" in result["signals"]
    assert "absent_from_sitemap" in result["signals"]


def test_no_target_queries_still_runs_orphan_detection():
    result = discover(
        seed_url="https://example.com/",
        seed_text="Welcome to Acme.",
        target_queries=None,
        internal_links=[],
        sitemap_urls=[],
        fetch_page_text=lambda _url: None,
        known_urls={"https://example.com/orphan"},
    )
    assert result["query_results"] == []
    assert result["orphaned_pages"]
