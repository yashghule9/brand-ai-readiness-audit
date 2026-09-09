"""Off-site corroboration: does a public record of this brand exist, and does
it agree with what the brand says about itself?

This is the only module that fetches anything other than the audited site. It
answers the entity-collision question directly: when a brand name is shared
with a larger entity, a retrieval pipeline resolves the name to whichever
candidate carries stronger corroboration, and a brand with no linked public
record loses that contest without ever being at fault on its own pages.

Deliberate constraints:

- **HTML only.** Wikipedia, Wikidata and the Wayback Machine are read by
  fetching and parsing rendered HTML, never their JSON APIs. That keeps the
  whole tool inside one access rule rather than carving out an exception,
  and it costs little: Wikipedia's own redirects resolve many name variants
  for free.
- **robots.txt is honoured here too.** These are third parties; the audit
  gets no special licence just because it is reading about a brand rather
  than crawling one.
- **Absence is only claimed after variants are exhausted.** "No entity
  record" is a false accusation if the entity exists under a former name, a
  parent, or a legal-name spelling — so every candidate is tried before
  absence is reported.
- **Every parse failure yields `unknown`.** These pages are third-party
  markup that changes without notice. A selector that stops matching must
  never turn into "the brand has no public record".
"""

from __future__ import annotations

import logging
import re
import urllib.parse
from typing import Any

import requests
from bs4 import BeautifulSoup

from braiaudit.fetch import DEFAULT_USER_AGENT, fetch_robots

logger = logging.getLogger("braiaudit.corroborate")

WIKIPEDIA = "https://en.wikipedia.org"
WIKIDATA = "https://www.wikidata.org"
WAYBACK = "https://web.archive.org"

# Requested as a date earlier than the web, so the Wayback Machine redirects
# to the earliest snapshot it holds and the timestamp lands in the final URL.
# This is the HTML path to a first-seen date; the availability JSON endpoint
# would be an API call.
_WAYBACK_EARLIEST = f"{WAYBACK}/web/19910101000000/"

_WAYBACK_TIMESTAMP = re.compile(r"/web/(\d{4})(\d{2})(\d{2})\d*/")

# Suffixes stripped when generating title candidates: a brand is far more
# likely to have an article under "Acme Foods" than "Acme Foods Ltd."
_LEGAL_SUFFIXES = re.compile(
    r"\b(inc|inc\.|llc|ltd|ltd\.|limited|plc|gmbh|s\.a\.|pvt|pvt\.|"
    r"private limited|corp|corp\.|corporation|co|co\.|company)\b\.?$",
    re.IGNORECASE,
)


def _title_candidates(brand_names: list[str], domain: str) -> list[str]:
    """Article titles worth trying, most specific first.

    Wikipedia resolves redirects on its own, so aliases and former names
    often need no special handling — but the legal-name and bare-name forms
    are generated explicitly because they are the variants a brand's own
    markup most often carries.
    """
    seen: set[str] = set()
    candidates: list[str] = []

    def add(value: str) -> None:
        value = " ".join(value.split()).strip(" -|·—")
        if not value or len(value) < 2:
            return
        key = value.lower()
        if key not in seen:
            seen.add(key)
            candidates.append(value)

    for name in brand_names:
        add(name)
        add(_LEGAL_SUFFIXES.sub("", name).strip())

    # The registrable label, as a last resort: "caterworld.ai" -> "Caterworld".
    label = domain.split(".")[0] if "." in domain else domain
    if label.startswith("www"):
        label = domain.split(".")[1] if domain.count(".") > 1 else label
    add(label.replace("-", " ").title())
    return candidates


class _Fetcher:
    """robots-honouring HTML GET with a per-run cache.

    Caches by resolved URL, so repeated candidate lookups and a re-run within
    one audit cost nothing, and robots.txt is read once per origin.
    """

    def __init__(self, session: requests.Session, user_agent: str, timeout: float = 10.0):
        self.session = session
        self.user_agent = user_agent
        self.timeout = timeout
        self.pages: dict[str, tuple[int, str, str]] = {}
        self.robots: dict[str, bool] = {}
        self.fetch_count = 0

    def _allowed(self, url: str) -> bool:
        parsed = urllib.parse.urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin not in self.robots:
            decision = fetch_robots(url, self.user_agent, self.session)
            self.robots[origin] = not decision.disallowed_for_agent
        return self.robots[origin]

    def get(self, url: str) -> tuple[int, str, str]:
        """(status, final_url, body). Status 0 means fetch failed or was
        disallowed — never treated as evidence of anything."""
        if url in self.pages:
            return self.pages[url]
        if not self._allowed(url):
            logger.info("corroboration: robots.txt disallows %s", url)
            result = (0, url, "")
        else:
            try:
                resp = self.session.get(
                    url,
                    timeout=self.timeout,
                    headers={"User-Agent": self.user_agent},
                    allow_redirects=True,
                )
                self.fetch_count += 1
                result = (resp.status_code, resp.url, resp.text)
            except requests.RequestException as exc:
                logger.info("corroboration: %s failed: %s", url, exc)
                result = (0, url, "")
        self.pages[url] = result
        return result


def _wikidata_id_from_article(html: str) -> str | None:
    """The Q-id linked from a Wikipedia article's sidebar."""
    soup = BeautifulSoup(html, "html.parser")
    link = soup.find("a", href=re.compile(r"wikidata\.org/wiki/(Special:EntityPage/)?Q\d+"))
    if not link:
        return None
    match = re.search(r"(Q\d+)", link["href"])
    return match.group(1) if match else None


def _wikidata_official_sites(html: str) -> list[str] | None:
    """Domains listed under the entity's *official website* property.

    Returns None only when the Statements section itself could not be
    located — a genuine parse failure on Wikidata's page structure. An
    empty list means the section parsed fine and simply declares no
    official-website statement, which is real, reportable evidence: many
    entities (a building material, a former or unrelated same-named thing,
    a company that has never enriched its record) legitimately have none.
    Collapsing that into "unknown" would hide the exact entity-collision
    case this detector exists to catch.

    Scoped deliberately to the P856 statement group rather than every
    external link on the page: reading all of them swept up citations and
    reference URLs, and made a real run against adobe.com report that its
    entity record "points to ac.uk" — a false accusation from a loose
    selector, not a real signal.
    """
    soup = BeautifulSoup(html, "html.parser")
    if soup.find(id="claims") is None:
        return None

    group = soup.find(id="P856") or soup.find(attrs={"data-property-id": "P856"})
    if group is None:
        return []

    hosts: list[str] = []
    for anchor in group.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href.startswith(("http://", "https://")):
            continue
        host = urllib.parse.urlparse(href).netloc.lower()
        if not host or any(w in host for w in ("wikimedia", "wikidata", "wikipedia")):
            continue
        hosts.append(host)
    return hosts


def _registrable(host: str) -> str:
    """Crude last-two-labels comparison, enough to match example.com against
    www.example.com without pulling in a public-suffix dependency."""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host.lower()


def corroborate(
    domain: str,
    brand_names: list[str],
    session: requests.Session | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
    max_candidates: int = 3,
) -> dict[str, Any]:
    """Look for a public entity record for this brand and compare it.

    Returns a signal bundle in the same shape every other producer emits, so
    the ontology classifies it without special handling.
    """
    session = session or requests.Session()
    fetcher = _Fetcher(session, user_agent)
    result: dict[str, Any] = {
        "domain": domain,
        "parse_status": "ok",
        "entity_found": False,
        "entity_url": "",
        "entity_reciprocal": False,
        "entity_evidence": "",
        "site_first_seen": "",
        "candidates_tried": "",
        "signals": [],
    }

    try:
        candidates = _title_candidates(brand_names, domain)[:max_candidates]
        result["candidates_tried"] = ", ".join(candidates)

        article_html = ""
        article_url = ""
        reached_source = False
        for title in candidates:
            url = f"{WIKIPEDIA}/wiki/{urllib.parse.quote(title.replace(' ', '_'))}"
            status, final_url, body = fetcher.get(url)
            if status:
                # A real HTTP answer, including a 404. Status 0 means blocked
                # by robots or a failed request: we never got to look.
                reached_source = True
            if status == 200 and body:
                article_html, article_url = body, final_url
                break

        if not article_html and not reached_source:
            # The source declined to be read, or could not be reached. That is
            # a gap in this audit, not evidence about the brand — claiming
            # absence here would be a false accusation built on our own
            # inability to look.
            result["parse_status"] = "unknown"
            result["entity_evidence"] = (
                "Public entity records could not be consulted (source unreachable "
                "or disallowed by its robots.txt), so nothing is claimed either way."
            )
            return result

        if not article_html:
            # Absence is only claimed here, after every candidate was tried.
            # Whether that is a small regional brand or a notability lag is a
            # judgement about prominence that no fetch can settle, so the
            # finding says absence and leaves the reason to a reader.
            result["entity_evidence"] = (
                "No Wikipedia article found for any of: "
                f"{result['candidates_tried']}. No public entity record links "
                f"{domain} to a distinguishable brand."
            )
            result["signals"].append("entity_record_absent")
            return result

        result["entity_found"] = True
        result["entity_url"] = article_url

        qid = _wikidata_id_from_article(article_html)
        official_hosts: list[str] | None = None
        if qid:
            status, entity_url, entity_html = fetcher.get(f"{WIKIDATA}/wiki/{qid}")
            if status == 200 and entity_html:
                result["entity_url"] = entity_url
                official_hosts = _wikidata_official_sites(entity_html)

        if official_hosts is None:
            # The entity exists, but its official-website property could not
            # be read. Reciprocity is undetermined, and an undetermined check
            # must not become an accusation that the record points elsewhere.
            result["parse_status"] = "unknown"
            result["entity_evidence"] = (
                f"A public entity record was found at {result['entity_url']}, but its "
                "official-website property could not be read, so whether it points "
                "back to this domain is undetermined."
            )
            return result

        want = _registrable(domain)
        result["entity_reciprocal"] = any(_registrable(h) == want for h in official_hosts)

        if result["entity_reciprocal"]:
            result["entity_evidence"] = (
                f"Public entity record {result['entity_url']} links back to {want}."
            )
        else:
            # An article exists under this brand's name but points somewhere
            # else — the entity-collision case, where the name resolves to a
            # better-corroborated stranger.
            others = ", ".join(sorted({_registrable(h) for h in official_hosts})[:3])
            result["entity_evidence"] = (
                f"A public entity record exists at {result['entity_url']} for this "
                f"brand name, but it does not link back to {want}"
                + (f" (it points to {others})" if others else " (it lists no official site)")
                + " — a name query may resolve to a different entity."
            )
            result["signals"].append("entity_record_not_reciprocal")
    except Exception as exc:  # noqa: BLE001 - unknown, never a false accusation
        result["parse_status"] = "unknown"
        result["signals"] = []
        logger.warning("corroboration could not be parsed for %s: %s", domain, exc)

    return result


def first_seen(
    domain: str,
    session: requests.Session | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> dict[str, Any]:
    """Earliest Wayback Machine snapshot of the domain, from the redirect
    target's URL rather than the availability API.

    Site age is weak evidence on its own and is reported as context rather
    than scored: a young domain is not a defect, it just carries less of the
    corroboration weight that entity resolution leans on.
    """
    session = session or requests.Session()
    fetcher = _Fetcher(session, user_agent)
    out: dict[str, Any] = {"domain": domain, "parse_status": "ok", "site_first_seen": ""}
    try:
        status, final_url, _ = fetcher.get(f"{_WAYBACK_EARLIEST}https://{domain}/")
        match = _WAYBACK_TIMESTAMP.search(final_url) if status else None
        if match:
            out["site_first_seen"] = f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    except Exception as exc:  # noqa: BLE001
        out["parse_status"] = "unknown"
        logger.warning("wayback lookup failed for %s: %s", domain, exc)
    return out
