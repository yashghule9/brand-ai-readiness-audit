"""Tests for the content-cleaner implementation (braiaudit.clean)."""

from __future__ import annotations

from braiaudit.clean import clean


def test_clean_article_extracts_prose_and_keeps_semantic_structure(fixture_html):
    html = fixture_html("clean_article.html")
    result = clean("https://example.com/blog/post", html)

    assert "Batch pre-staging" in result["clean_text"]
    assert "torque-guided" in result["clean_text"].lower()
    # nav/footer boilerplate must not leak into the extracted main text
    assert "Privacy" not in result["clean_text"]
    assert result["structure"]["semantic_tags_present"] is True
    assert result["structure"]["headings_found"] >= 3
    assert "zero_semantic_tags" not in result["signals"]
    assert result["main_text_to_markup_ratio"] > 0


def test_boilerplate_heavy_page_strips_nav_footer_and_cookie_banner(fixture_html):
    html = fixture_html("boilerplate_heavy.html")
    result = clean("https://example.com/", html)

    assert "Home" not in result["clean_text"]
    assert "Privacy" not in result["clean_text"]
    assert "cookies to improve" not in result["clean_text"].lower()
    assert any(o["type"] == "cookie" for o in result["overlays_removed"])
    assert "cookie_banner_detected" in result["signals"]
    assert result["boilerplate_removed_bytes"] > 0


def test_content_hash_is_stable_for_identical_text(fixture_html):
    html = fixture_html("clean_article.html")
    first = clean("https://example.com/a", html)
    second = clean("https://example.com/b", html)
    assert first["content_hash"] == second["content_hash"]


def test_empty_page_yields_empty_clean_text():
    result = clean("https://example.com/blank", "<html><body></body></html>")
    assert result["clean_text"] == ""
    assert result["main_text_to_markup_ratio"] == 0.0


# --- Language gating for the question-register check -----------------------
# The register check matches English question words only, so a page the site
# itself declares non-English must not be flagged for language.

def test_french_page_with_english_question_words_not_flagged():
    from braiaudit.clean import clean

    html = (
        '<html lang="fr"><body><main>'
        "<h1>What is notre produit?</h1>"
        "<p>" + "Notre produit est excellent et abordable. " * 10 + "</p>"
        "</main></body></html>"
    )
    result = clean("https://example.fr/", html)
    assert "no_question_shaped_headings" not in result["signals"]


def test_english_page_register_check_preserved():
    from braiaudit.clean import clean

    html = (
        '<html lang="en"><body><main>'
        "<h1>Pure brand voice, no questions here</h1>"
        "<p>" + "Our product leads the market. " * 10 + "</p>"
        "</main></body></html>"
    )
    result = clean("https://example.com/", html)
    assert "no_question_shaped_headings" in result["signals"]


def test_missing_lang_attribute_keeps_register_check():
    from braiaudit.clean import clean

    html = (
        "<html><body><main>"
        "<h1>No declared language, brand-voice headings</h1>"
        "<p>" + "We build things customers love. " * 10 + "</p>"
        "</main></body></html>"
    )
    result = clean("https://example.com/", html)
    assert "no_question_shaped_headings" in result["signals"]
