"""Tests for the staleness / identity / engagement detectors: structured-data
freshness, entity linkage, schema-vs-visual desync, question register, and
whether concrete answers sit above the fold."""

from __future__ import annotations

import json

from bs4 import BeautifulSoup

from braiaudit.clean import clean
from braiaudit.fetch import _json_ld_blocks, analyse_structured_data


def _soup(*json_ld: dict) -> BeautifulSoup:
    scripts = "".join(
        f'<script type="application/ld+json">{json.dumps(block)}</script>' for block in json_ld
    )
    return BeautifulSoup(f"<html><body>{scripts}</body></html>", "html.parser")


def test_json_ld_blocks_flattens_graphs_and_survives_malformed_blocks():
    html = """
    <script type="application/ld+json">{"@graph": [{"@type": "Organization"},
                                                   {"@type": "WebSite"}]}</script>
    <script type="application/ld+json">{ this is not json </script>
    <script type="application/ld+json">[{"@type": "Product"}]</script>
    """
    blocks = _json_ld_blocks(BeautifulSoup(html, "html.parser"))
    types = {t for b in blocks for t in (b.get("@type"),)}
    assert types == {"Organization", "WebSite", "Product"}


def test_freshness_date_detected_from_either_property():
    assert analyse_structured_data(
        [{"@type": "Article", "dateModified": "2024-03-01"}], ""
    )["has_freshness_date"]
    assert not analyse_structured_data([{"@type": "Article"}], "")["has_freshness_date"]


def test_organization_without_same_as_is_an_unlinked_entity():
    without = analyse_structured_data([{"@type": "Organization", "name": "Garuda"}], "garuda")
    assert without["has_organization"] and not without["has_same_as"]

    with_links = analyse_structured_data(
        [{"@type": "Organization", "name": "Garuda", "sameAs": ["https://www.wikidata.org/wiki/Q1"]}],
        "garuda",
    )
    assert with_links["has_same_as"]


def test_schema_visual_desync_catches_a_retired_tagline():
    """The rebrand-desync case: the visible page was updated, the JSON-LD
    behind it was not, and nobody reviewing the page in a browser can see it."""
    blocks = [{"@type": "Organization", "name": "Garuda", "slogan": "Engineered For Flight"}]
    visible_now = "Garuda Footwear. Run Your Own Race. Running shoes for India."

    result = analyse_structured_data(blocks, visible_now)
    assert result["desynced_properties"] == ["slogan='Engineered For Flight'"]

    # Once the markup matches what a reader sees, nothing is reported.
    visible_then = "Garuda Footwear. Engineered For Flight. Running shoes."
    assert analyse_structured_data(blocks, visible_then)["desynced_properties"] == []


def test_headings_written_as_questions_clear_the_register_check():
    slogans = (
        "<html><body><main><h2>Engineered For Flight</h2><h2>Built To Last</h2>"
        + "<p>Marketing copy. </p>" * 30
        + "</main></body></html>"
    )
    assert "no_question_shaped_headings" in clean("https://e.com/", slogans)["signals"]

    questions = (
        "<html><body><main><h2>How much does the Velocity X9 cost?</h2>"
        "<h2>Which shoe suits marathon training</h2>"
        + "<p>Answer copy. </p>" * 30
        + "</main></body></html>"
    )
    result = clean("https://e.com/", questions)
    assert "no_question_shaped_headings" not in result["signals"]
    assert result["structure"]["question_shaped_headings"] == 2


def test_concrete_answers_below_the_fold_are_flagged():
    prose = "Garuda designs footwear for the way India actually runs. " * 40
    buried = f"<html><body><main><p>{prose}</p><p>Price: 5999 rupees.</p></main></body></html>"
    assert "primary_facts_below_fold" in clean("https://e.com/", buried)["signals"]

    lead_with_it = (
        f"<html><body><main><p>The Velocity X9 costs 5999 rupees.</p><p>{prose}</p>"
        "</main></body></html>"
    )
    assert "primary_facts_below_fold" not in clean("https://e.com/", lead_with_it)["signals"]


def test_a_page_with_no_numbers_at_all_is_not_flagged():
    """The check asks whether an existing answer is buried, never whether the
    page should have had one — an about page with no numbers is not a defect."""
    prose = "<p>" + "Garuda designs footwear for how India runs. " * 40 + "</p>"
    html = f"<html><body><main>{prose}</main></body></html>"
    assert "primary_facts_below_fold" not in clean("https://e.com/", html)["signals"]
