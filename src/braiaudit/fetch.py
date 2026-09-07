"""Implementation of the `website-observer` skill.

A cheap, JavaScript-free HTTP inspection pass: robots.txt / sitemap.xml
resolution, a single GET of the target URL, and static-HTML metrics. See
skills/website-observer/SKILL.md for the full step-by-step spec this
mirrors.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass, field
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from braiaudit.schemas import validate

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
    "page not found",
    "404 not found",
    "item unavailable",
    "this page doesn't exist",
    "we couldn't find that page",
    "content not found",
)

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


def _origin(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def fetch_robots(url: str, user_agent: str, session: requests.Session) -> RobotsDecision:
    robots_url = urllib.parse.urljoin(_origin(url), "/robots.txt")
    rp = urllib.robotparser.RobotFileParser()
    try:
        resp = session.get(robots_url, timeout=10)
        if resp.status_code >= 400:
            return RobotsDecision(fetched=False, disallowed_for_agent=False,
                                   crawl_delay_seconds=None, sitemap_urls=[])
        rp.parse(resp.text.splitlines())
    except requests.RequestException:
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


def fetch_sitemap_urls(origin: str, declared: list[str], session: requests.Session) -> list[str]:
    """Fetch sitemap(s) and return every <loc> URL found. Best-effort: a
    missing or malformed sitemap yields an empty list, never an exception."""
    candidates = declared or [urllib.parse.urljoin(origin, "/sitemap.xml")]
    urls: list[str] = []
    for sitemap_url in candidates:
        try:
            resp = session.get(sitemap_url, timeout=10)
            if resp.status_code != 200:
                continue
            root = ElementTree.fromstring(resp.content)
            ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
            locs = root.findall(".//sm:loc", ns) or root.findall(".//loc")
            urls.extend(loc.text.strip() for loc in locs if loc.text)
        except (requests.RequestException, ElementTree.ParseError):
            continue
    return urls


def _detect_anti_bot(status_code: int, headers: dict[str, str], body: str) -> str | None:
    """The matched bot-mitigation fingerprint, or None. Returning which
    fingerprint matched (rather than a bare bool) lets the finding say what
    was actually seen instead of restating the signal name."""
    if status_code not in (403, 503):
        return None
    haystack = (body[:5000] + " " + " ".join(headers.values())).lower()
    return next((fp for fp in _ANTI_BOT_FINGERPRINTS if fp in haystack), None)


def _detect_soft_404(status_code: int, title: str, body_sample: str) -> bool:
    if status_code != 200:
        return False
    haystack = f"{title} {body_sample}".lower()
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

    return {
        "schema_types": sorted(types),
        "has_freshness_date": has_date,
        "has_organization": bool(organizations),
        "has_same_as": bool(same_as),
        "desynced_properties": desynced,
    }


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
    origin_cache: dict[str, tuple[RobotsDecision, list[str]]] | None = None,
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
        "ai_crawler_access": {},
        "blocked_ai_crawlers": "",
        "anti_bot_evidence": "",
        "internal_links": [],
        "signals": [],
    }

    origin = _origin(url)
    cached = origin_cache.get(origin) if origin_cache is not None else None
    if cached is None:
        robots = fetch_robots(url, user_agent, session)
        sitemap_urls = fetch_sitemap_urls(origin, robots.sitemap_urls, session)
        if origin_cache is not None:
            origin_cache[origin] = (robots, sitemap_urls)
    else:
        robots, sitemap_urls = cached

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
        if retry_after and retry_after.isdigit():
            time.sleep(min(float(retry_after), 5))
            resp = session.get(url, timeout=timeout, headers={"User-Agent": user_agent})
            result["http_status"] = resp.status_code
        if resp.status_code == 429:
            result["signals"].append("http_429_rate_limit")
            validate(result, "website-observer")
            return result

    body = resp.text if "text" in (result["content_type"] or "text/html") else ""

    fingerprint = _detect_anti_bot(resp.status_code, result["headers"], body)
    if fingerprint:
        result["anti_bot_evidence"] = (
            f"HTTP {resp.status_code} carrying the bot-mitigation fingerprint "
            f"{fingerprint!r} — the page a crawler receives is a challenge, not content"
        )
        result["signals"].append("anti_bot_challenge_detected")
        validate(result, "website-observer")
        return result

    if not body or "html" not in (result["content_type"] or ""):
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
    result["soft_404_suspected"] = _detect_soft_404(resp.status_code, title, body[:3000])

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
    if result["soft_404_suspected"]:
        signals.append("soft_404_suspected")

    validate(result, "website-observer")
    return result
