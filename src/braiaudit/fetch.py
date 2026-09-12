"""Implementation of the `website-observer` skill.

A cheap, JavaScript-free HTTP inspection pass: robots.txt / sitemap.xml
resolution, a single GET of the target URL, and static-HTML metrics. See
skills/website-observer/SKILL.md for the full step-by-step spec this
mirrors.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from braiaudit.schemas import validate

logger = logging.getLogger("braiaudit.fetch")

DEFAULT_USER_AGENT = "BrandAIReadinessAuditBot/1.0 (+https://example.com/bot)"

# The crawlers that actually feed AI answer engines. A site can be perfectly
# crawlable by classic search and still be invisible to every assistant if
# robots.txt names these agents — the single most common root cause of "we
# don't appear in ChatGPT/Gemini." Evaluated against the already-parsed
# robots.txt, so checking all of them costs zero extra requests.
AI_CRAWLER_USER_AGENTS: dict[str, str] = {
    "GPTBot": "OpenAI — ChatGPT retrieval and training",
    "OAI-SearchBot": "OpenAI — ChatGPT search index",
    "ChatGPT-User": "OpenAI — user-initiated page fetch",
    "ClaudeBot": "Anthropic — Claude retrieval",
    "anthropic-ai": "Anthropic — legacy agent token",
    "PerplexityBot": "Perplexity — search index",
    "Google-Extended": "Google — Gemini grounding and AI Overviews",
    "Applebot-Extended": "Apple — Apple Intelligence",
    "CCBot": "Common Crawl — feeds many training corpora",
}

# Classic search crawlers, used only as a contrast set: allowing these while
# disallowing the agents above is a deliberate-looking AI opt-out, and worth
# saying so explicitly in the finding's evidence.
_CLASSIC_CRAWLER_USER_AGENTS = ("Googlebot", "Bingbot")

# Extensions never worth spending a crawl budget slot on.
_NON_HTML_EXTENSIONS = (
    ".pdf", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico",
    ".mp4", ".webm", ".mp3", ".zip", ".gz", ".css", ".js", ".json",
    ".xml", ".rss", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)

_ANTI_BOT_FINGERPRINTS = (
    "cf-mitigated",
    "checking your browser",
    "attention required! | cloudflare",
    "akamai",
    "captcha",
    "please verify you are a human",
    "sorry, you have been blocked",
)

_SOFT_404_PHRASES = (
    # Application-level: a client-side router serving its not-found UI at 200.
    "page not found",
    "404 not found",
    "item unavailable",
    "this page doesn't exist",
    "we couldn't find that page",
    "content not found",
    # Hosting-level: the origin never resolved the vhost at all, and the edge
    # returned its own stub at 200. Found live on a real .ac.in apex serving
    # "404 Unknown host", which the audit then read as the institution's
    # homepage and produced seven content findings from.
    "unknown host",
    "no such host",
    "default backend - 404",
)

# Two thresholds, because the two checks separate different things.
#
# At status 200 the question is "is this an error UI wearing a success
# status, or a page *about* errors?" The rival is an HTTP-status
# documentation article — MDN's own 404 page is titled "404 Not Found" and
# runs to ~11,900 characters — so the bar sits well above any error UI and
# an order of magnitude below the article. A branded not-found page at 200
# is genuinely a soft 404 and is meant to be caught here.
_SOFT_404_MAX_TEXT = 1200

# At an error status the question is different: the status line is already
# honest, so the only thing to establish is whether a body worth reading came
# back. The rival here is a *genuine* branded 404 — nav, an explanation, links
# onward — which is a real page of the site and must keep being analysed. So
# this bar is tight: it clears the default server stubs (nginx's "404 Not
# Found nginx/1.18.0", Apache's ErrorDocument sentence, a bare vhost "404
# Unknown host" at 104 characters) and nothing else.
_ERROR_STUB_MAX_TEXT = 250

_ROOT_CONTAINER_IDS = ("app", "root", "__next", "___gatsby")

# Visible "last updated" style markers, checked when JSON-LD carries no
# dateModified. Some assistants weigh a visible freshness cue too.
_VISIBLE_FRESHNESS_PATTERN = re.compile(
    r"\b(last\s+updated|updated\s+on|last\s+modified|revised\s+on)\b", re.IGNORECASE
)

# schema.org properties whose value is a short brand-identity string. When one
# of these is absent from the page a human reads, the structured data has
# drifted from the visible site — the rebrand-desync failure mode.
_IDENTITY_STRING_PROPERTIES = ("slogan", "alternateName", "legalName")

# A page that quotes prices or asks for a purchase is one an assistant gets
# asked factual questions about, so Product/Offer markup genuinely matters
# there. On an about or policy page it does not, and reporting its absence at
# the same severity is a false positive.
_COMMERCE_PATTERNS = re.compile(
    r"(add to (cart|bag)|buy now|shop now|in stock|out of stock|free shipping"
    r"|[$£€₹]\s?\d[\d,.]*|(usd|eur|gbp|inr)\s?\d)",
    re.IGNORECASE,
)

_LOW_RAW_TEXT_THRESHOLD = 500
_HIGH_SCRIPT_COUNT_THRESHOLD = 15


@dataclass
class RobotsDecision:
    fetched: bool
    disallowed_for_agent: bool
    crawl_delay_seconds: float | None
    sitemap_urls: list[str]
    # {agent: allowed?} for every agent in AI_CRAWLER_USER_AGENTS.
    ai_agent_access: dict[str, bool] = field(default_factory=dict)
    # {agent: allowed?} for the classic-search contrast set.
    classic_agent_access: dict[str, bool] = field(default_factory=dict)


def normalize_url(target: str) -> str:
    if not urllib.parse.urlparse(target).scheme:
        target = f"https://{target}"
    parsed = urllib.parse.urlparse(target)
    if not parsed.path:
        parsed = parsed._replace(path="/")
    return urllib.parse.urlunparse(parsed)


def site_label(target: str) -> str:
    """The bare host for a domain or full URL: 'example.com'.

    Accepts what a person types on a command line — 'example.com',
    'https://www.example.com/', 'http://example.com/path' — so the report's
    `site` field is a domain rather than whatever spelling was passed in.
    """
    parsed = urllib.parse.urlparse(normalize_url(target))
    return parsed.netloc or target


# Public suffixes with more than one label, where the last two labels are the
# *suffix* and carry no registrable name. Without this, every `*.gov.in`
# reduces to "gov.in" and two unrelated organisations (isro.gov.in and
# nasa.gov.in) compare as the same domain — a false equivalence that silently
# masks a real mismatch. Not a full public-suffix list, and deliberately not a
# new dependency: this covers the suffixes that appear in this project's own
# corpus plus the common English-language ones.
_MULTIPART_SUFFIXES = frozenset(
    {
        "gov.in", "co.in", "ac.in", "net.in", "org.in", "edu.in", "res.in", "nic.in",
        "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "ltd.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "com.br", "com.cn", "com.mx", "com.sg", "com.my", "com.tr", "com.tw",
        "co.jp", "co.kr", "co.nz", "co.za", "com.hk", "com.ph",
    }
)


def registrable_domain(host: str) -> str:
    """The registrable domain: one label above the public suffix.

    `www.example.com` and `example.com` both reduce to `example.com`, while
    `isro.gov.in` and `nasa.gov.in` stay distinct rather than both collapsing
    to `gov.in`. Single source of truth for every caller that needs to decide
    whether two hostnames belong to the same site.
    """
    parts = host.lower().strip(".").split(".")
    if len(parts) < 2:
        return host.lower()
    if len(parts) >= 3 and ".".join(parts[-2:]) in _MULTIPART_SUFFIXES:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _load_robots_parser(
    url: str, session: requests.Session
) -> urllib.robotparser.RobotFileParser | None:
    """Fetch and parse /robots.txt once per origin; None when absent/unreadable.

    The parser is cached so `can_fetch` can be re-evaluated per URL: a robots
    file that allows `/` but disallows `/private/` is only honoured correctly
    if the verdict is recomputed for each page's path, not frozen at the first
    URL that happened to hit this origin.
    """
    robots_url = urllib.parse.urljoin(_origin(url), "/robots.txt")
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = session.get(robots_url, timeout=10)
        if resp.status_code >= 400:
            return None
        rp.parse(resp.text.splitlines())
        return rp
    except requests.RequestException:
        return None


def fetch_robots(url: str, user_agent: str, session: requests.Session) -> RobotsDecision:
    return _decide_robots(_load_robots_parser(url, session), url, user_agent)


def _decide_robots(
    rp: urllib.robotparser.RobotFileParser | None, url: str, user_agent: str
) -> RobotsDecision:
    if rp is None:
        return RobotsDecision(fetched=False, disallowed_for_agent=False,
                               crawl_delay_seconds=None, sitemap_urls=[])

    disallowed = not rp.can_fetch(user_agent, url)
    delay = rp.crawl_delay(user_agent)
    sitemaps = list(rp.site_maps() or [])
    return RobotsDecision(
        fetched=True,
        disallowed_for_agent=disallowed,
        crawl_delay_seconds=float(delay) if delay is not None else None,
        sitemap_urls=sitemaps,
        # Same parsed file, no extra requests — see AI_CRAWLER_USER_AGENTS.
        ai_agent_access={a: rp.can_fetch(a, url) for a in AI_CRAWLER_USER_AGENTS},
        classic_agent_access={a: rp.can_fetch(a, url) for a in _CLASSIC_CRAWLER_USER_AGENTS},
    )


def fetch_sitemap_urls(
    origin: str, declared: list[str], session: requests.Session
) -> tuple[list[str], list[str]]:
    """Fetch sitemap(s) and return (every <loc> URL, every <lastmod> value).

    Best-effort: a missing or malformed sitemap yields empty lists, never an
    exception. lastmod comes back because a sitemap whose dates are absent or
    all identical tells a crawler nothing about what changed, which is a
    staleness signal in its own right.
    """
    candidates = declared or [urllib.parse.urljoin(origin, "/sitemap.xml")]
    urls: list[str] = []
    lastmods: list[str] = []
    for sitemap_url in candidates:
        try:
            resp = session.get(sitemap_url, timeout=10)
            if resp.status_code != 200:
                continue
            root = ElementTree.fromstring(resp.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = root.findall(".//sm:loc", ns) or root.findall(".//loc")
            urls.extend(loc.text.strip() for loc in locs if loc.text)
            mods = root.findall(".//sm:lastmod", ns) or root.findall(".//lastmod")
            lastmods.extend(m.text.strip() for m in mods if m.text)
        except (requests.RequestException, ElementTree.ParseError):
            continue
    return urls, lastmods


def _detect_anti_bot(status_code: int, headers: dict[str, str], body: str) -> str | None:
    """The matched bot-mitigation fingerprint, or None. Returning which
    fingerprint matched (rather than a bare bool) lets the finding say what
    was actually seen instead of restating the signal name."""
    if status_code not in (403, 503):
        return None
    haystack = (body[:5000] + " " + " ".join(headers.values())).lower()
    return next((fp for fp in _ANTI_BOT_FINGERPRINTS if fp in haystack), None)


def _detect_soft_404(status_code: int, title: str, visible_text: str) -> bool:
    """Whether a 200 response is really an error stub.

    Two deliberate narrowings, because a false positive here is expensive:
    a page detected as a soft 404 is halted, so its content is never analysed
    and a single-page site stops being scored at all.

    First, the haystack is the *visible text*, not raw HTML. Searching markup
    matched on a `/page-not-found` href, a CSS class, or a phrase buried in
    inline JSON — none of which is something a reader ever sees.

    Second, a phrase alone is not enough. "404 Not Found" appearing in a
    thousand-word article is a page *about* HTTP status codes, not a page
    that is one; only a short body corroborates that the phrase is the page's
    whole message rather than its subject.
    """
    if status_code != 200:
        return False
    if len(visible_text) > _SOFT_404_MAX_TEXT:
        return False
    haystack = f"{title} {visible_text}".lower()
    return any(phrase in haystack for phrase in _SOFT_404_PHRASES)


def _extract_internal_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    """Absolute, deduplicated, same-host page links found in static HTML.

    Without this the pipeline can only learn a site's link graph from the
    optional render backend, so on a Playwright-less run every multi-page
    check (fragmentation, orphan detection) silently has nothing to work
    with. Fragments are stripped and asset URLs dropped so the crawl
    frontier doesn't spend budget re-fetching one page under many spellings.
    """
    base_host = urllib.parse.urlparse(base_url).netloc.lower()
    links: set[str] = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        absolute = urllib.parse.urljoin(base_url, href)
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in ("http", "https"):
            continue
        if parsed.netloc.lower() != base_host:
            continue
        if parsed.path.lower().endswith(_NON_HTML_EXTENSIONS):
            continue
        links.add(canonical_crawl_url(absolute))
    return sorted(links)


def canonical_crawl_url(url: str) -> str:
    """One URL, one spelling: no fragment, no redundant trailing slash.

    `/pricing`, `/pricing/` and `/pricing#plans` are the same page to a
    crawler, and counting them as three would inflate the link graph and
    waste the page budget.
    """
    parsed = urllib.parse.urlparse(url)._replace(fragment="")
    path = parsed.path or "/"
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/") or "/"
    return urllib.parse.urlunparse(parsed._replace(path=path))


def _json_ld_blocks(soup: BeautifulSoup) -> list[dict]:
    """Every parseable JSON-LD object on the page, @graph entries flattened.

    Malformed JSON-LD is common and is not itself what this audit reports,
    so a block that will not parse is skipped rather than raised.
    """
    blocks: list[dict] = []
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            parsed = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            graph = candidate.get("@graph")
            if isinstance(graph, list):
                blocks.extend(node for node in graph if isinstance(node, dict))
            else:
                blocks.append(candidate)
    return blocks


def _types_of(block: dict) -> set[str]:
    raw = block.get("@type") or block.get("type") or []
    values = raw if isinstance(raw, list) else [raw]
    return {str(v).split("/")[-1] for v in values}


def analyse_structured_data(blocks: list[dict], visible_text: str) -> dict[str, Any]:
    """Freshness, entity-identity and schema/visual-desync facts from JSON-LD.

    Returns plain facts; the caller turns them into signals. `visible_text`
    is the page's rendered text, used to check that what the structured data
    claims is also what a human actually sees.
    """
    types: set[str] = set()
    for block in blocks:
        types |= _types_of(block)

    has_date = any(b.get("dateModified") or b.get("datePublished") for b in blocks)
    organizations = [b for b in blocks if _types_of(b) & {"Organization", "Corporation", "Brand"}]
    same_as = [b for b in organizations if b.get("sameAs")]

    haystack = visible_text.lower()
    desynced: list[str] = []
    for block in organizations:
        for prop in _IDENTITY_STRING_PROPERTIES:
            value = block.get(prop)
            if isinstance(value, str) and len(value) > 3 and value.lower() not in haystack:
                desynced.append(f"{prop}={value!r}")

    # legalName and sameAs are already read above; retaining the values
    # rather than only the booleans exposes facts the pipeline had observed
    # and was discarding. No signal derives from these — they exist so a
    # consumer can see what the site declares about itself.
    legal_name = ""
    for block in organizations:
        candidate = block.get("legalName")
        if isinstance(candidate, str) and candidate.strip():
            legal_name = candidate.strip()
            break

    same_as_urls: list[str] = []
    for block in organizations:
        raw = block.get("sameAs")
        values = raw if isinstance(raw, list) else [raw]
        same_as_urls.extend(v.strip() for v in values if isinstance(v, str) and v.strip())

    return {
        "schema_types": sorted(types),
        "has_freshness_date": has_date,
        "has_organization": bool(organizations),
        "has_same_as": bool(same_as),
        "desynced_properties": desynced,
        "legal_name": legal_name,
        "same_as": sorted(set(same_as_urls)),
    }


_COPYRIGHT_YEAR = re.compile(
    r"(?:\u00a9|&copy;|copyright)\s*(?:\d{4}\s*[-\u2013]\s*)?(\d{4})", re.I
)
_VISIBLE_YEAR = re.compile(r"\b(20\d{2})\b")

# How stale a Last-Modified header has to be before it is worth reporting.
# Long enough that a normally-maintained site never trips it.
_STALE_HEADER_DAYS = 540

# A page with this much prose and almost no headings reads as a wall of text.
_WALL_OF_TEXT_CHARS = 2500
_WALL_OF_TEXT_CHARS_PER_HEADING = 1200


def _footer_copyright_year(text: str) -> int | None:
    """The most recent year in a copyright notice, or None if there is none.

    Takes the maximum rather than the first match: a page can carry both a
    template notice and an article date, and the newest is the one that
    reflects maintenance.
    """
    years = [int(m) for m in _COPYRIGHT_YEAR.findall(text)]
    return max(years) if years else None


def _header_age_days(last_modified: str | None) -> int | None:
    """Age in days of a Last-Modified header, or None if absent/unparseable."""
    if not last_modified:
        return None
    try:
        stamp = parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return None
    if stamp is None:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - stamp).days


def _brand_names(soup: BeautifulSoup, blocks: list[dict]) -> dict[str, str]:
    """Brand name as stated in each place a machine might read it."""
    names: dict[str, str] = {}
    og = soup.find("meta", attrs={"property": "og:site_name"})
    if og and og.get("content"):
        names["og:site_name"] = og["content"].strip()
    for block in blocks:
        if _types_of(block) & {"Organization", "Corporation", "Brand"}:
            name = block.get("name")
            if isinstance(name, str) and name.strip():
                names["Organization.name"] = name.strip()
                break
    return names


def _has_contact_details(soup: BeautifulSoup, blocks: list[dict]) -> bool:
    """Whether any machine-readable way to contact or locate the operator
    exists — a phone or mail link, a postal address, or ContactPoint markup.

    Entity resolution leans on this: a consistent name-address-phone
    signature is what separates a brand from others sharing its name.
    """
    if soup.find("a", href=lambda h: bool(h) and h.startswith(("tel:", "mailto:"))):
        return True
    if soup.find("address"):
        return True
    for block in blocks:
        if block.get("address") or block.get("telephone") or block.get("contactPoint"):
            return True
        if _types_of(block) & {"ContactPoint", "PostalAddress"}:
            return True
    return False


def _render_blocking_scripts(soup: BeautifulSoup) -> int:
    """Scripts in <head> with neither async nor defer — they block parsing.

    A lab proxy: it measures markup, not the load time a visitor experiences,
    and is labelled `lab` in the ontology so it is never read as a field
    measurement of performance.
    """
    head = soup.head
    if head is None:
        return 0
    blocking = 0
    for tag in head.find_all("script", src=True):
        if tag.get("async") is None and tag.get("defer") is None:
            blocking += 1
    return blocking


def _detect_root_container(soup: BeautifulSoup) -> bool:
    for container_id in _ROOT_CONTAINER_IDS:
        el = soup.find(id=container_id)
        if el is not None and len(el.get_text(strip=True)) < 40:
            return True
    return False


def observe(
    url: str,
    user_agent: str = DEFAULT_USER_AGENT,
    timeout_ms: int = 10000,
    session: requests.Session | None = None,
    origin_cache: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the full website-observer pass for one URL. Returns a dict
    validated against schemas/website-observer.output.schema.json.

    `origin_cache` memoises robots.txt and sitemap.xml per origin for the
    life of one audit. Both are per-site documents, so refetching them for
    every page of a crawl is pure waste — two extra round trips per page,
    which on a slow host is most of the crawl's wall-clock time.
    """
    url = normalize_url(url)
    session = session or requests.Session()
    timeout = timeout_ms / 1000

    result: dict[str, Any] = {
        "url": url,
        "http_status": None,
        "headers": {},
        "response_time_ms": None,
        "sitemap_present": False,
        "raw_html_bytes": None,
        "raw_text_length": None,
        "script_count": None,
        "link_count": None,
        "root_container_detected": False,
        "data_src_attribute_present": False,
        "canonical_tag_present": False,
        "json_ld_present": False,
        "soft_404_suspected": False,
        "content_type": None,
        "final_url": None,
        "redirect_count": None,
        "schema_types": [],
        "structured_data_desync": "",
        "commerce_page_detected": False,
        "parse_status": "ok",
        "footer_copyright_year": None,
        "last_modified_age_days": None,
        "sitemap_lastmod_state": "",
        "meta_description_present": False,
        "h1_count": 0,
        "breadcrumb_present": False,
        "render_blocking_script_count": 0,
        "brand_name_variants": "",
        "brand_name_candidates": [],
        "declared_brand_name": "",
        "organization_legal_name": "",
        "organization_same_as": [],
        "ai_crawler_access": {},
        "blocked_ai_crawlers": "",
        "anti_bot_evidence": "",
        "http_error_evidence": "",
        "internal_links": [],
        "signals": [],
    }

    origin = _origin(url)
    cached = origin_cache.get(origin) if origin_cache is not None else None
    if cached is None:
        robots_parser = _load_robots_parser(url, session)
        sitemap_urls, sitemap_lastmods = fetch_sitemap_urls(
            origin, list(robots_parser.site_maps() or []) if robots_parser else [], session
        )
        if origin_cache is not None:
            origin_cache[origin] = (robots_parser, sitemap_urls, sitemap_lastmods)
    else:
        robots_parser, sitemap_urls, sitemap_lastmods = cached
    # Per-URL decision, always recomputed: disallow verdicts are path-scoped,
    # so reusing the seed's verdict for every later page on this origin would
    # fetch URLs the robots file explicitly disallows.
    robots = _decide_robots(robots_parser, url, user_agent)

    result["robots_txt"] = {
        "fetched": robots.fetched,
        "disallowed_for_agent": robots.disallowed_for_agent,
        "crawl_delay_seconds": robots.crawl_delay_seconds,
        "sitemap_urls": robots.sitemap_urls,
    }
    result["sitemap_present"] = bool(sitemap_urls)
    result["sitemap_urls"] = sitemap_urls

    # Evaluated before the robots short-circuit below: which AI crawlers this
    # site turns away is worth reporting even when our own agent is also
    # blocked and the rest of the pass never runs.
    result["ai_crawler_access"] = robots.ai_agent_access
    blocked = sorted(a for a, allowed in robots.ai_agent_access.items() if not allowed)
    if blocked:
        allowed_classic = sorted(
            a for a, allowed in robots.classic_agent_access.items() if allowed
        )
        contrast = (
            f", while still allowing {', '.join(allowed_classic)}" if allowed_classic else ""
        )
        result["blocked_ai_crawlers"] = (
            f"robots.txt disallows {len(blocked)} AI crawler(s): "
            f"{', '.join(blocked)}{contrast}"
        )
        result["signals"].append("ai_crawler_robots_disallow")

    if robots.disallowed_for_agent:
        result["signals"].append("robots_txt_disallow")
        validate(result, "website-observer")
        return result

    if robots.crawl_delay_seconds:
        time.sleep(min(robots.crawl_delay_seconds, 5))

    try:
        started = time.monotonic()
        resp = session.get(
            url, timeout=timeout, headers={"User-Agent": user_agent}, allow_redirects=True
        )
        result["response_time_ms"] = round((time.monotonic() - started) * 1000, 1)
    except requests.RequestException as exc:
        result["error"] = str(exc)
        result["signals"].append("connection_failed")
        validate(result, "website-observer")
        return result

    result["http_status"] = resp.status_code
    result["headers"] = dict(resp.headers)
    result["final_url"] = resp.url
    result["redirect_count"] = len(resp.history)
    result["content_type"] = resp.headers.get("Content-Type")

    if result["redirect_count"] and result["redirect_count"] > 3:
        result["signals"].append("excessive_redirects")

    if resp.status_code == 429:
        retry_after = resp.headers.get("Retry-After")
        # Retry only when the server asked for a short wait we can honour in
        # this process. A Retry-After of minutes or hours must be taken at its
        # word: sleeping capped at 5s and re-GETting anyway would hit the host
        # seconds after it explicitly asked for an hour — the opposite of the
        # politeness this crawler promises.
        if retry_after and retry_after.isdigit() and float(retry_after) <= 5:
            time.sleep(float(retry_after))
            resp = session.get(url, timeout=timeout, headers={"User-Agent": user_agent})
            result["http_status"] = resp.status_code
        if resp.status_code == 429:
            result["signals"].append("http_429_rate_limit")
            validate(result, "website-observer")
            return result

    # `application/xhtml+xml` carries no "text" substring but is real HTML;
    # excluding its body here silently dropped such 200 pages with zero
    # signals — a false clean.
    ct = result["content_type"] or "text/html"
    body = resp.text if ("text" in ct or "html" in ct or "xml" in ct) else ""

    fingerprint = _detect_anti_bot(resp.status_code, result["headers"], body)
    if fingerprint:
        result["anti_bot_evidence"] = (
            f"HTTP {resp.status_code} carrying the bot-mitigation fingerprint "
            f"{fingerprint!r} — the page a crawler receives is a challenge, not content"
        )
        result["signals"].append("anti_bot_challenge_detected")
        validate(result, "website-observer")
        return result

    has_usable_body = bool(body) and "html" in (result["content_type"] or "")

    if not has_usable_body:
        # A 4xx/5xx with no usable body falls through every other detector:
        # it is not 429 (handled above), and no anti-bot fingerprint matched
        # (many blocking responses carry no informative header or body at
        # all — this was found live against a real site returning a bare
        # 403 with zero bytes). Left unhandled, this produced zero signals
        # and the page still counted as successfully crawled: a false clean
        # built entirely on our own inability to retrieve anything.
        if resp.status_code >= 400:
            result["http_error_evidence"] = (
                f"HTTP {resp.status_code} with no retrievable content — the page "
                "could not be read, for a reason this audit cannot determine "
                "(access control, geographic or automated-traffic blocking, or a "
                "genuine server error)"
            )
            result["signals"].append("http_error_status_blocked")
        validate(result, "website-observer")
        return result

    # A body exists, looks like HTML, and carries no recognised anti-bot
    # fingerprint — but the status is 403 or 503, the same pair
    # `_detect_anti_bot` treats as bot-mitigation-relevant. This body could
    # be a challenge page worded in a way our fingerprint list doesn't know,
    # or it could be a legitimate response that happens to use one of these
    # statuses (a paywall teaser, an age-gate, a region notice) while still
    # carrying real content. Nothing observable here can tell those apart
    # reliably, and guessing wrong in either direction is a real cost:
    # analysing a block page as the site fabricates content-quality findings
    # about a page that was never actually seen; assuming every such
    # response is blocked would falsely accuse a legitimate one. So this is
    # reported as an explicit "could not confirm" limitation — scored
    # nowhere, analysed no further — rather than guessed either way.
    if resp.status_code in (403, 503):
        result["http_error_evidence"] = (
            f"HTTP {resp.status_code} returned a response body, but no confirmed "
            "bot-mitigation fingerprint matched it. This audit cannot determine "
            "whether the body is a challenge page in unfamiliar wording or "
            "genuine content served with this status, so it was not analysed "
            "as the site's real content."
        )
        result["signals"].append("http_error_status_unconfirmed")
        validate(result, "website-observer")
        return result

    result["raw_html"] = body
    result["raw_html_bytes"] = len(resp.content)
    soup = BeautifulSoup(body, "html.parser")

    script_tags = soup.find_all("script")
    result["script_count"] = len(script_tags)
    result["json_ld_present"] = any(
        tag.get("type") == "application/ld+json" for tag in script_tags
    )
    link_count = len(soup.find_all("a", href=True))
    result["internal_links"] = _extract_internal_links(soup, result["final_url"] or url)
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator=" ", strip=True)
    result["raw_text_length"] = len(text)
    result["link_count"] = link_count
    result["canonical_tag_present"] = soup.find("link", rel="canonical") is not None
    result["root_container_detected"] = _detect_root_container(soup)
    result["data_src_attribute_present"] = soup.find(attrs={"data-src": True}) is not None

    title = soup.title.get_text(strip=True) if soup.title else ""
    # `text`, not `body`: the reader's view of the page, with script/style
    # already stripped above — see _detect_soft_404 for why markup is the
    # wrong haystack.
    result["soft_404_suspected"] = _detect_soft_404(resp.status_code, title, text)
    if result["soft_404_suspected"]:
        # Same contract as the 403/503 path above: once this response is
        # established to be an error stub, it stops being observed for
        # content. Reporting "no structured data, no contact details, no
        # headings" about a stub would be literally true of the stub and
        # entirely false of the site — which is exactly how a real .ac.in
        # apex serving "404 Unknown host" collected seven findings that
        # belonged to no one. The structural fields measured above stay,
        # because they describe what came back; the content signals never
        # fire, because there is no content here to judge.
        result["signals"].append("soft_404_suspected")
        validate(result, "website-observer")
        return result

    # The same situation wearing an honest status line. A 4xx/5xx whose body
    # is only a stub is not the site's content either, and until now only two
    # narrower cases were caught: a *bodiless* error (handled above) and
    # 403/503 specifically. A 404 carrying a short stub fell between them and
    # went through full content analysis — which is how a real .ac.in apex
    # returning 104 bytes of "404 Unknown host" was read as an institution's
    # homepage and charged with seven content and identity defects. Length is
    # the test, not the status: a genuine error page with a real body (nav,
    # copy, suggested links) is still the site's own page and still analysed.
    if resp.status_code >= 400 and len(text) <= _ERROR_STUB_MAX_TEXT:
        result["http_error_evidence"] = (
            f"HTTP {resp.status_code} returned only {len(text)} character(s) of "
            "text — an error stub, not a page. Nothing here describes the site, "
            "so it was not analysed as the site's content."
        )
        result["signals"].append("http_error_status_blocked")
        validate(result, "website-observer")
        return result

    signals = result["signals"]
    if result["raw_text_length"] < _LOW_RAW_TEXT_THRESHOLD:
        signals.append("low_raw_text")
    if result["script_count"] > _HIGH_SCRIPT_COUNT_THRESHOLD:
        signals.append("high_script_count")
    if result["root_container_detected"]:
        signals.append("root_container_detected")
    if result["data_src_attribute_present"]:
        signals.append("data_src_attribute_present")
    schema = analyse_structured_data(_json_ld_blocks(soup), text)
    result["schema_types"] = schema["schema_types"]
    result["organization_legal_name"] = schema["legal_name"]
    result["organization_same_as"] = schema["same_as"]

    # --- staleness / engagement / identity ------------------------------
    # Wrapped as one unit: these all parse markup that varies wildly between
    # sites, and a detector that cannot read what it expected must report
    # "unknown" rather than "absent". Absence is an accusation; unknown is
    # an admission, and only the second is honest when parsing failed.
    try:
        blocks = _json_ld_blocks(soup)

        year = _footer_copyright_year(text)
        result["footer_copyright_year"] = year
        if year is not None and year < datetime.now(timezone.utc).year:
            signals.append("stale_copyright_year")

        age = _header_age_days(result["headers"].get("Last-Modified"))
        result["last_modified_age_days"] = age
        if age is None or age > _STALE_HEADER_DAYS:
            signals.append("last_modified_stale_or_absent")

        if not sitemap_lastmods:
            result["sitemap_lastmod_state"] = (
                "sitemap declares no <lastmod> dates, so a crawler cannot tell "
                "which pages changed"
            )
            signals.append("sitemap_lastmod_meaningless")
        elif len(set(sitemap_lastmods)) == 1 and len(sitemap_lastmods) > 1:
            result["sitemap_lastmod_state"] = (
                f"all {len(sitemap_lastmods)} sitemap <lastmod> values are "
                f"identical ({sitemap_lastmods[0]}), which carries no "
                "per-page change information"
            )
            signals.append("sitemap_lastmod_meaningless")

        schema_years = {
            int(y)
            for b in blocks
            for v in (b.get("dateModified"), b.get("datePublished"))
            if isinstance(v, str)
            for y in _VISIBLE_YEAR.findall(v)
        }
        if schema_years and year is not None and year > max(schema_years):
            signals.append("visible_date_contradicts_schema")

        h1s = soup.find_all("h1")
        result["h1_count"] = len(h1s)
        title_text = (soup.title.get_text(strip=True) if soup.title else "").strip()
        if not h1s or not title_text:
            signals.append("no_page_identifying_heading")

        crumb = soup.find(attrs={"aria-label": re.compile("breadcrumb", re.I)}) or soup.find(
            class_=re.compile("breadcrumb", re.I)
        )
        has_crumb_schema = any("BreadcrumbList" in _types_of(b) for b in blocks)
        result["breadcrumb_present"] = bool(crumb or has_crumb_schema)
        depth = len([p for p in urllib.parse.urlparse(url).path.split("/") if p])
        if depth >= 2 and not result["breadcrumb_present"]:
            signals.append("no_breadcrumb_trail")

        desc = soup.find("meta", attrs={"name": re.compile("^description$", re.I)})
        result["meta_description_present"] = bool(desc and (desc.get("content") or "").strip())
        if not result["meta_description_present"]:
            signals.append("meta_description_absent")

        result["render_blocking_script_count"] = _render_blocking_scripts(soup)
        if result["render_blocking_script_count"] > 3:
            signals.append("render_blocking_scripts")

        orgs = [b for b in blocks if _types_of(b) & {"Organization", "Corporation", "Brand"}]
        if orgs and not any(b.get("logo") for b in orgs):
            signals.append("organization_logo_missing")

        names = _brand_names(soup, blocks)
        if title_text:
            names["title"] = title_text
        # Ordered most-authoritative first: schema and og:site_name state the
        # brand deliberately, a <title> merely contains it.
        result["brand_name_candidates"] = [
            names[k] for k in ("Organization.name", "og:site_name", "title") if names.get(k)
        ]
        # Only Organization.name and og:site_name are deliberate declarations
        # of what the brand is called. A <title> is a page title that often
        # merely contains the brand ("Zerodha: Online brokerage platform for
        # stock trading & investing"), so it is kept out of this field — a
        # consumer treating a title as a brand name would ask worse questions
        # than one falling back to the domain label.
        result["declared_brand_name"] = (
            names.get("Organization.name") or names.get("og:site_name") or ""
        )
        distinct = {v.lower().strip() for v in names.values() if v}
        if len(names) >= 2 and len(distinct) > 1:
            # Only report when no stated name contains another: "Acme" inside
            # "Acme | Pricing" is normal titling, not an inconsistency.
            values = sorted(distinct)
            if not any(a != b and a in b for a in values for b in values):
                result["brand_name_variants"] = "; ".join(
                    f"{k}={v!r}" for k, v in sorted(names.items())
                )
                signals.append("brand_name_inconsistent")

        if not _has_contact_details(soup, blocks):
            signals.append("contact_details_absent")
    except Exception as exc:  # noqa: BLE001 - unknown, never a false accusation
        result["parse_status"] = "unknown"
        logger.warning("secondary detectors could not parse %s: %s", url, exc)

    commerce_page = bool(_COMMERCE_PATTERNS.search(text))
    result["commerce_page_detected"] = commerce_page
    product_types = {"Product", "Offer", "AggregateOffer", "ProductGroup"}
    if commerce_page and not (product_types & set(result["schema_types"] or ())):
        # Prices present, no Product/Offer markup: the exact case where an
        # assistant has to infer a price from prose, which is where wrong
        # prices in AI answers come from.
        signals.append("missing_product_schema")
    elif not result["json_ld_present"]:
        # No structured data at all, on a page with nothing transactional to
        # describe — worth reporting, but not at the same severity.
        signals.append("missing_schema_org")

    if result["json_ld_present"]:
        # Only meaningful when structured data exists at all — a site with no
        # JSON-LD is already reported once, and piling on adds no information.
        if not schema["has_freshness_date"] and not _VISIBLE_FRESHNESS_PATTERN.search(text):
            signals.append("freshness_markers_absent")
        if schema["has_organization"] and not schema["has_same_as"]:
            signals.append("entity_sameas_missing")
        if schema["desynced_properties"]:
            result["structured_data_desync"] = (
                "structured data states "
                + "; ".join(schema["desynced_properties"])
                + " but none of those strings appear in the page a reader sees"
            )
            signals.append("schema_visual_desync")
    if not result["canonical_tag_present"]:
        signals.append("canonical_missing")
    # No soft-404 branch here: reaching this line means the response was not
    # a stub, since a detected one returns early above with that signal as
    # its only conclusion.

    validate(result, "website-observer")
    return result
