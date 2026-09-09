"""Off-site corroboration: entity existence, reciprocity, and the honesty
guarantees around them. Absence is only claimed after variants are exhausted,
and any parse failure yields `unknown` rather than a false accusation."""

from __future__ import annotations

import requests
import responses

from braiaudit.corroborate import (
    _registrable,
    _title_candidates,
    _wikidata_id_from_article,
    _wikidata_official_sites,
    corroborate,
)

ROBOTS_DISALLOW_ALL = "User-agent: *" + chr(10) + "Disallow: /" + chr(10)
ROBOTS_OK = "User-agent: *\nAllow: /\n"


def _article_linking(qid: str) -> str:
    """Minimal Wikipedia article markup carrying a Wikidata sidebar link."""
    return (
        '<html><body><a href="https://www.wikidata.org/wiki/'
        + qid
        + '">item</a></body></html>'
    )


def _allow_robots():
    for origin in ("https://en.wikipedia.org", "https://www.wikidata.org"):
        responses.add(responses.GET, f"{origin}/robots.txt", body=ROBOTS_OK, status=200)


def test_title_candidates_strip_legal_suffixes_and_fall_back_to_the_domain():
    candidates = _title_candidates(["Acme Foods Ltd"], "acmefoods.com")
    assert "Acme Foods Ltd" in candidates
    assert "Acme Foods" in candidates  # the form an article is likelier under
    assert "Acmefoods" in candidates  # last-resort domain label

    # No brand names at all still yields something to try.
    assert _title_candidates([], "caterworld.ai") == ["Caterworld"]


def test_registrable_matches_www_against_bare_domain():
    assert _registrable("www.example.com") == _registrable("example.com")
    assert _registrable("example.com") != _registrable("example.org")


def test_wikidata_id_and_official_sites_parse_from_rendered_html():
    article = _article_linking("Q42")
    assert _wikidata_id_from_article(article) == "Q42"
    assert _wikidata_id_from_article("<html><body>no link</body></html>") is None

    entity = """
    <html><body>
      <h2 id="claims">Statements</h2>
      <div id="P856"><a href="https://acmefoods.com/">acmefoods.com</a></div>
      <div id="P2013"><a href="https://facebook.com/acme">facebook</a></div>
      <div><a href="https://en.wikipedia.org/wiki/Acme_Foods_Award">a citation</a></div>
    </body></html>
    """
    hosts = _wikidata_official_sites(entity)
    # Only the official-website statement, not every external link on the
    # page — a citation elsewhere must never count as "points to".
    assert hosts == ["acmefoods.com"]

    # Statements section parsed fine, but no P856 group in it: the entity
    # genuinely declares no official site. Real evidence, not a parse miss.
    no_website = '<html><body><h2 id="claims">Statements</h2></body></html>'
    assert _wikidata_official_sites(no_website) == []

    # No Statements section at all is the genuine parse failure.
    assert _wikidata_official_sites("<html><body>not a wikidata page</body></html>") is None


@responses.activate
def test_absence_is_only_claimed_after_every_variant_is_tried():
    _allow_robots()
    for title in ("Acme_Foods_Ltd", "Acme_Foods", "Acmefoods"):
        responses.add(
            responses.GET, f"https://en.wikipedia.org/wiki/{title}", status=404
        )

    result = corroborate(
        "acmefoods.com", ["Acme Foods Ltd"], session=requests.Session()
    )

    assert result["signals"] == ["entity_record_absent"]
    assert "Acme Foods Ltd" in result["candidates_tried"]
    assert "Acme Foods" in result["candidates_tried"]
    tried = [c.request.url for c in responses.calls if "/wiki/" in c.request.url]
    assert len(tried) >= 2, "gave up before exhausting name variants"


@responses.activate
def test_a_record_pointing_elsewhere_is_the_collision_case():
    """An article exists under the brand's name but lists someone else's
    site: the name resolves to a better-corroborated stranger."""
    _allow_robots()
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/wiki/Garuda",
        body=_article_linking("Q7"),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.wikidata.org/wiki/Q7",
        body=(
            '<html><body><h2 id="claims">Statements</h2>'
            '<div id="P856"><a href="https://garuda-indonesia.com/">site</a></div>'
            "</body></html>"
        ),
        status=200,
    )

    result = corroborate("garudafootwear.in", ["Garuda"], session=requests.Session())

    assert result["entity_found"] is True
    assert result["entity_reciprocal"] is False
    assert result["signals"] == ["entity_record_not_reciprocal"]
    assert "garuda-indonesia.com" in result["entity_evidence"]


@responses.activate
def test_a_reciprocal_record_raises_nothing():
    _allow_robots()
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/wiki/Acme_Foods",
        body=_article_linking("Q9"),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.wikidata.org/wiki/Q9",
        body=(
            '<html><body><h2 id="claims">Statements</h2>'
            '<div id="P856"><a href="https://www.acmefoods.com/">site</a></div>'
            "</body></html>"
        ),
        status=200,
    )

    result = corroborate("acmefoods.com", ["Acme Foods"], session=requests.Session())

    assert result["entity_reciprocal"] is True
    assert result["signals"] == []


@responses.activate
def test_a_source_that_declines_to_be_read_yields_unknown_not_absence():
    """The sharpest honesty case: being unable to look is a gap in this
    audit, not evidence the brand has no public record. Reporting absence
    here would be a false accusation built on our own blindness."""
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/robots.txt",
        body=ROBOTS_DISALLOW_ALL,
        status=200,
    )

    result = corroborate("acmefoods.com", ["Acme Foods"], session=requests.Session())

    assert result["parse_status"] == "unknown"
    assert result["signals"] == [], "claimed absence without being able to look"
    assert not [c for c in responses.calls if "/wiki/" in c.request.url]


@responses.activate
def test_a_genuine_404_still_reports_absence():
    """The contrast: the source answered, and the answer was "no such
    article". That is real evidence and must still be reported."""
    _allow_robots()
    for title in ("Acme_Foods", "Acmefoods"):
        responses.add(responses.GET, f"https://en.wikipedia.org/wiki/{title}", status=404)

    result = corroborate("acmefoods.com", ["Acme Foods"], session=requests.Session())

    assert result["parse_status"] == "ok"
    assert result["signals"] == ["entity_record_absent"]


@responses.activate
def test_undeterminable_reciprocity_is_unknown_not_a_collision_finding():
    """The property block could not be located at all. This is different
    from the entity declaring no official site: it is this parser failing
    to read the page, and must not become "points elsewhere"."""
    _allow_robots()
    responses.add(
        responses.GET,
        "https://en.wikipedia.org/wiki/Acme_Foods",
        body=_article_linking("Q99"),
        status=200,
    )
    responses.add(
        responses.GET,
        "https://www.wikidata.org/wiki/Q99",
        body="<html><body>markup with no P856 statement block at all</body></html>",
        status=200,
    )

    result = corroborate("acmefoods.com", ["Acme Foods"], session=requests.Session())

    assert result["parse_status"] == "unknown"
    assert result["signals"] == []


@responses.activate
def test_first_seen_reads_the_wayback_redirect_target_not_an_api():
    """The HTML path: request an impossibly early snapshot, let the
    redirect land on the earliest one that exists, and read the date out of
    the final URL rather than calling the availability endpoint."""
    from braiaudit.corroborate import first_seen

    responses.add(
        responses.GET,
        "https://web.archive.org/web/19910101000000/https://example.com/",
        status=200,
        body="snapshot page",
    )
    # `responses` doesn't rewrite `resp.url` on its own; patch requests to
    # report the redirect target the way a real 3xx chain would.
    import unittest.mock

    class _Resp:
        status_code = 200
        text = "ok"
        url = "https://web.archive.org/web/20240315120000/https://example.com/"

    with unittest.mock.patch("requests.Session.get", return_value=_Resp()):
        result = first_seen("example.com")

    assert result["site_first_seen"] == "2024-03-15"
    assert result["parse_status"] == "ok"
