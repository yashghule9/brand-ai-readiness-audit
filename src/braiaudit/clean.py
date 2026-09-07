"""Implementation of the `content-cleaner` skill.

Strips boilerplate and interrupt overlays from HTML, scores candidate
main-content containers with a lightweight readability heuristic, and
reports the resulting text-to-markup ratio and structural signals. See
skills/content-cleaner/SKILL.md for the full spec.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from bs4 import BeautifulSoup
from bs4.element import Tag

from braiaudit.schemas import validate

_BOILERPLATE_TAGS = ("nav", "header", "footer", "aside")
_BOILERPLATE_ROLES = ("navigation", "banner", "contentinfo")
_BOILERPLATE_CLASS_HINTS = ("sidebar", "site-nav", "site-footer", "breadcrumbs")

_OVERLAY_KEYWORDS = ("cookie", "consent", "newsletter", "subscribe", "paywall", "modal", "overlay")
_Z_INDEX_THRESHOLD = 1000

_LOW_RATIO_THRESHOLD = 0.15
_HIGH_BOILERPLATE_BYTES_FRACTION = 0.40

_SEMANTIC_CONTENT_TAGS = ("article", "section", "h1", "h2", "h3", "h4", "h5", "h6")

# People ask assistants questions; brands write headings as slogans. A page
# whose headings never take a question's shape is written in the wrong
# register to be retrieved for one.
_QUESTION_WORDS = ("what", "how", "why", "when", "where", "which", "who", "is", "does", "can")

# How much of the main text counts as "above the fold" for the purpose of
# asking whether the concrete answer (a price, a size, a number) is near the
# top or buried under marketing copy.
# Prose this long with almost no headings is a wall of text: a reader
# arriving cold cannot scan it, and a chunker has no natural split points.
_WALL_OF_TEXT_CHARS = 2500
_CHARS_PER_HEADING = 1200

_ABOVE_FOLD_CHARS = 600
_MIN_LENGTH_FOR_FOLD_CHECK = 1500


def _z_index_of(tag: Tag) -> int | None:
    style = tag.get("style", "") or ""
    match = re.search(r"z-index\s*:\s*(\d+)", style, re.IGNORECASE)
    return int(match.group(1)) if match else None


def _is_overlay(tag: Tag) -> tuple[bool, int | None]:
    style = (tag.get("style") or "").lower()
    positioned = "position:fixed" in style.replace(" ", "") or "position: fixed" in style
    z_index = _z_index_of(tag)
    high_z = z_index is not None and z_index >= _Z_INDEX_THRESHOLD
    classes_and_id = " ".join([*(tag.get("class") or []), tag.get("id") or ""]).lower()
    keyword_match = any(k in classes_and_id for k in _OVERLAY_KEYWORDS)
    is_overlay = (positioned or high_z) and keyword_match
    return is_overlay, z_index


def _is_boilerplate(tag: Tag) -> bool:
    if tag.name in _BOILERPLATE_TAGS:
        return True
    role = (tag.get("role") or "").lower()
    if role in _BOILERPLATE_ROLES:
        return True
    classes_and_id = " ".join([*(tag.get("class") or []), tag.get("id") or ""]).lower()
    return any(hint in classes_and_id for hint in _BOILERPLATE_CLASS_HINTS)


def _text_density_score(tag: Tag) -> float:
    """Readability-style heuristic: text length minus a penalty for link
    density and nesting depth, favoring long, low-link-ratio blocks."""
    text = tag.get_text(" ", strip=True)
    text_len = len(text)
    if text_len < 25:
        return 0.0
    link_text_len = sum(len(a.get_text(" ", strip=True)) for a in tag.find_all("a"))
    link_density = link_text_len / text_len if text_len else 1.0
    return text_len * (1 - min(link_density, 0.9))


def _find_main_content(soup: BeautifulSoup) -> Tag | None:
    explicit = soup.find("article") or soup.find("main")
    if explicit is not None and len(explicit.get_text(strip=True)) > 40:
        return explicit

    candidates = soup.find_all(["div", "section"])
    scored = [(c, _text_density_score(c)) for c in candidates]
    scored = [(c, s) for c, s in scored if s > 0]
    if not scored:
        return soup.body or soup
    return max(scored, key=lambda pair: pair[1])[0]


def clean(url: str, html: str, source: str = "raw") -> dict[str, Any]:
    """Run the full content-cleaner pass. Returns a dict validated against
    schemas/content-cleaner.output.schema.json."""
    original_bytes = len(html.encode("utf-8"))
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    for comment in soup.find_all(string=lambda s: s.__class__.__name__ == "Comment"):
        comment.extract()

    overlays_removed: list[dict[str, Any]] = []
    for tag in list(soup.find_all(True)):
        if tag.decomposed:
            continue
        is_overlay, z_index = _is_overlay(tag)
        if is_overlay:
            classes_and_id = " ".join([*(tag.get("class") or []), tag.get("id") or ""]).lower()
            overlay_type = next(
                (k for k in _OVERLAY_KEYWORDS if k in classes_and_id), "overlay"
            )
            overlays_removed.append({"type": overlay_type, "z_index": z_index})
            tag.decompose()

    boilerplate_bytes_before = len(str(soup).encode("utf-8"))
    for tag in list(soup.find_all(True)):
        if not tag.decomposed and _is_boilerplate(tag):
            tag.decompose()
    boilerplate_removed_bytes = boilerplate_bytes_before - len(str(soup).encode("utf-8"))

    main = _find_main_content(soup)
    clean_text = main.get_text("\n", strip=True) if main is not None else ""

    ratio = (len(clean_text) / original_bytes) if original_bytes else 0.0
    headings = main.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]) if main is not None else []
    paragraphs = main.find_all("p") if main is not None else []
    semantic_present = bool(main is not None and main.find_all(_SEMANTIC_CONTENT_TAGS))

    content_hash = "sha256:" + hashlib.sha256(clean_text.encode("utf-8")).hexdigest()

    heading_texts = [h.get_text(" ", strip=True) for h in headings]
    question_shaped = [
        h
        for h in heading_texts
        if "?" in h or h.lower().split()[:1] and h.lower().split()[0] in _QUESTION_WORDS
    ]
    above_fold = clean_text[:_ABOVE_FOLD_CHARS]

    signals: list[str] = []
    if len(clean_text) > _WALL_OF_TEXT_CHARS and (
        not headings or len(clean_text) / len(headings) > _CHARS_PER_HEADING
    ):
        signals.append("wall_of_text_structure")
    if heading_texts and not question_shaped:
        signals.append("no_question_shaped_headings")
    if (
        len(clean_text) > _MIN_LENGTH_FOR_FOLD_CHECK
        and any(ch.isdigit() for ch in clean_text)
        and not any(ch.isdigit() for ch in above_fold)
    ):
        signals.append("primary_facts_below_fold")
    if ratio < _LOW_RATIO_THRESHOLD:
        signals.append("low_main_text_ratio")
    boilerplate_fraction = boilerplate_removed_bytes / original_bytes if original_bytes else 0
    if boilerplate_fraction > _HIGH_BOILERPLATE_BYTES_FRACTION:
        signals.append("high_nav_footer_density")
    if not semantic_present and len(clean_text) > 200:
        signals.append("zero_semantic_tags")
    for overlay in overlays_removed:
        if overlay["type"] == "cookie":
            signals.append("cookie_banner_detected")
        else:
            signals.append("high_z_index_overlay_present")

    result = {
        "url": url,
        "clean_text": clean_text,
        "main_text_to_markup_ratio": round(ratio, 4),
        "boilerplate_removed_bytes": max(boilerplate_removed_bytes, 0),
        "structure": {
            "semantic_tags_present": semantic_present,
            "headings_found": len(headings),
            "paragraphs_found": len(paragraphs),
            "question_shaped_headings": len(question_shaped),
        },
        "overlays_removed": overlays_removed,
        "content_hash": content_hash,
        "truncated": False,
        "signals": sorted(set(signals)),
    }
    validate(result, "content-cleaner")
    return result
