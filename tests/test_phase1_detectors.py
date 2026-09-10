"""Phase 1 detectors: staleness, engagement and identity signals parsed from
HTML and headers already fetched. Every one must fail to `unknown` rather
than reporting absence — a parse failure that reads as a fault is a false
accusation on the exact axis being strengthened."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from bs4 import BeautifulSoup

from braiaudit.clean import clean
from braiaudit.fetch import (
    _brand_names,
    _footer_copyright_year,
    _has_contact_details,
    _header_age_days,
    _json_ld_blocks,
    _render_blocking_scripts,
)


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


# --- staleness ------------------------------------------------------------


def test_copyright_year_takes_the_most_recent_notice():
    """A page can carry a template notice and an article date; the newest is
    the one that reflects maintenance."""
    assert _footer_copyright_year("\u00a9 2019 Acme. Updated \u00a9 2024 Acme") == 2024
    assert _footer_copyright_year("Copyright 2016-2021 Acme Ltd") == 2021
    assert _footer_copyright_year("All rights reserved.") is None


def test_last_modified_age_survives_unparseable_headers():
    recent = (datetime.now(timezone.utc) - timedelta(days=3)).strftime(
        "%a, %d %b %Y %H:%M:%S GMT"
    )
    assert _header_age_days(recent) == 3
    # Absent or malformed must be None, which the caller treats as "no signal
    # of freshness", never as "fresh".
    assert _header_age_days(None) is None
    assert _header_age_days("not a date") is None


# --- engagement -----------------------------------------------------------


def test_render_blocking_scripts_counts_only_unhinted_head_scripts():
    html = """
    <html><head>
      <script src="/a.js"></script>
      <script src="/b.js" defer></script>
      <script src="/c.js" async></script>
      <script>inline()</script>
    </head><body><script src="/d.js"></script></body></html>
    """
    # Only /a.js: deferred, async, inline and body scripts do not block.
    assert _render_blocking_scripts(_soup(html)) == 1


def test_wall_of_text_needs_both_length_and_missing_structure():
    prose = "Sentences about the product that go on for a while. " * 70
    unstructured = f"<html><body><main><p>{prose}</p></main></body></html>"
    assert "wall_of_text_structure" in clean("https://e.com/", unstructured)["signals"]

    headed = "".join(f"<h2>Section {i}</h2><p>{prose[:400]}</p>" for i in range(6))
    structured = f"<html><body><main>{headed}</main></body></html>"
    assert "wall_of_text_structure" not in clean("https://e.com/", structured)["signals"]

    # Short prose without headings is not a wall of text.
    short = "<html><body><main><p>One short paragraph.</p></main></body></html>"
    assert "wall_of_text_structure" not in clean("https://e.com/", short)["signals"]


# --- identity -------------------------------------------------------------


def test_brand_name_variants_are_collected_from_every_stated_source():
    html = """
    <html><head><meta property="og:site_name" content="Acme Foods">
    <script type="application/ld+json">
      {"@type": "Organization", "name": "Acme Foods Ltd"}
    </script></head><body></body></html>
    """
    soup = _soup(html)
    names = _brand_names(soup, _json_ld_blocks(soup))
    assert names["og:site_name"] == "Acme Foods"
    assert names["Organization.name"] == "Acme Foods Ltd"


def test_contact_details_accept_any_machine_readable_form():
    assert _has_contact_details(_soup('<a href="tel:+441234">call</a>'), [])
    assert _has_contact_details(_soup("<address>1 High St</address>"), [])
    assert _has_contact_details(_soup("<p>none</p>"), [{"telephone": "+441234"}])
    assert _has_contact_details(_soup("<p>none</p>"), [{"@type": "PostalAddress"}])

    # A contact *page link* is not contact detail — nothing machine-readable.
    assert not _has_contact_details(_soup('<a href="/contact">Contact us</a>'), [])
