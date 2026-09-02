"""Implementation of the `website-observer` skill.

A cheap, JavaScript-free HTTP inspection pass: robots.txt / sitemap.xml
resolution, a single GET of the target URL, and static-HTML metrics. See
skills/website-observer/SKILL.md for the full step-by-step spec this
mirrors.
"""

from __future__ import annotations

import time
import urllib.parse
import urllib.robotparser
from dataclasses import dataclass
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

from braiaudit.schemas import validate

DEFAULT_USER_AGENT = "BrandAIReadinessAuditBot/1.0 (+https://example.com/bot)"

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

_LOW_RAW_TEXT_THRESHOLD = 500
_HIGH_SCRIPT_COUNT_THRESHOLD = 15


@dataclass
class RobotsDecision:
    fetched: bool
    disallowed_for_agent: bool
    crawl_delay_seconds: float | None
    sitemap_urls: list[str]


def normalize_url(target: str) -> str:
    if not urllib.parse.urlparse(target).scheme:
        target = f"https://{target}"
    parsed = urllib.parse.urlparse(target)
    if not parsed.path:
        parsed = parsed._replace(path="/")
    return urllib.parse.urlunparse(parsed)


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


def _detect_anti_bot(status_code: int, headers: dict[str, str], body: str) -> bool:
    if status_code not in (403, 503):
        return False
    haystack = (body[:5000] + " " + " ".join(headers.values())).lower()
    return any(fp in haystack for fp in _ANTI_BOT_FINGERPRINTS)


def _detect_soft_404(status_code: int, title: str, body_sample: str) -> bool:
    if status_code != 200:
        return False
    haystack = f"{title} {body_sample}".lower()
    return any(phrase in haystack for phrase in _SOFT_404_PHRASES)


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
) -> dict[str, Any]:
    """Run the full website-observer pass for one URL. Returns a dict
    validated against schemas/website-observer.output.schema.json."""
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
        "signals": [],
    }

    robots = fetch_robots(url, user_agent, session)
    result["robots_txt"] = {
        "fetched": robots.fetched,
        "disallowed_for_agent": robots.disallowed_for_agent,
        "crawl_delay_seconds": robots.crawl_delay_seconds,
        "sitemap_urls": robots.sitemap_urls,
    }
    sitemap_urls = fetch_sitemap_urls(_origin(url), robots.sitemap_urls, session)
    result["sitemap_present"] = bool(sitemap_urls)
    result["sitemap_urls"] = sitemap_urls

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

    if _detect_anti_bot(resp.status_code, result["headers"], body):
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
    if not result["json_ld_present"]:
        signals.append("missing_schema_org")
    if not result["canonical_tag_present"]:
        signals.append("canonical_missing")
    if result["soft_404_suspected"]:
        signals.append("soft_404_suspected")

    validate(result, "website-observer")
    return result
