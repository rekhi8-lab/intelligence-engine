"""
Social Scraper — IG + LinkedIn trend signals via Google Search.

Collects publicly indexed content from Instagram and LinkedIn by
searching Google with site: operators. This avoids:
  - Logging into either platform
  - Using any private API or scraping endpoint
  - Violating ToS (we only read what Google has already indexed)

Output format matches listener_brain.py signal schema:
  {"source": "instagram_google" | "linkedin_google", "text": "...", "signal": N}

Add to listener_brain.py's collection pipeline or run standalone.
"""

from __future__ import annotations

import re
import time
import requests
from typing import Any

# ─────────────────────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────────────────────

# Google Custom Search JSON API (free tier: 100 queries/day)
# Set these in .env or pass directly
# If not available, falls back to a simpler approach
import os as _os
from dotenv import load_dotenv as _load_dotenv


def _load_keys():
    _load_dotenv(
        dotenv_path=_os.path.join(_os.path.dirname(__file__), ".env"),
        override=False,
    )
    global GOOGLE_CSE_API_KEY, GOOGLE_CSE_CX, SERPER_API_KEY
    GOOGLE_CSE_API_KEY = _os.getenv("GOOGLE_CSE_API_KEY")
    GOOGLE_CSE_CX = _os.getenv("GOOGLE_CSE_CX")
    SERPER_API_KEY = _os.getenv("SERPER_API_KEY")


_load_keys()

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )
}

# Search queries tailored to your niche
IG_QUERIES = [
    "site:instagram.com menopause awareness",
    "site:instagram.com perimenopause symptoms",
    "site:instagram.com endometriosis support",
    "site:instagram.com PCOS hormonal health",
    "site:instagram.com women ADHD diagnosis",
    "site:instagram.com hormonal imbalance natural",
    "site:instagram.com ayurveda women health",
]

LINKEDIN_QUERIES = [
    "site:linkedin.com menopause workplace",
    "site:linkedin.com women health startup",
    "site:linkedin.com perimenopause awareness",
    "site:linkedin.com endometriosis advocacy",
    "site:linkedin.com PCOS research",
    "site:linkedin.com hormonal health women",
    "site:linkedin.com ayurveda wellness",
    "site:linkedin.com women health founder",
]


# ─────────────────────────────────────────────────────────────
# GOOGLE CUSTOM SEARCH API (preferred — structured results)
# ─────────────────────────────────────────────────────────────

def _search_google_cse(query: str, num: int = 5) -> list[dict]:
    """Use Google Custom Search JSON API. Free tier: 100 queries/day."""
    if not GOOGLE_CSE_API_KEY or not GOOGLE_CSE_CX:
        return []

    url = "https://www.googleapis.com/customsearch/v1"
    params = {
        "key": GOOGLE_CSE_API_KEY,
        "cx": GOOGLE_CSE_CX,
        "q": query,
        "num": min(num, 10),
    }
    try:
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        items = resp.json().get("items", [])
        results = []
        for item in items:
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })
        return results
    except Exception as e:
        print(f"    [CSE] {query[:40]}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# SERPER API FALLBACK (free tier: 2500 queries/month)
# ─────────────────────────────────────────────────────────────


def _search_serper(query: str, num: int = 5) -> list[dict]:
    """Use Serper.dev Google Search API as alternative."""
    if not SERPER_API_KEY:
        _load_keys()

    if not SERPER_API_KEY:
        return []

    try:
        resp = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=15,
        )
        resp.raise_for_status()
        results = []
        for item in resp.json().get("organic", []):
            results.append({
                "title": item.get("title", ""),
                "snippet": item.get("snippet", ""),
                "link": item.get("link", ""),
            })
        return results
    except Exception as e:
        print(f"    [Serper] {query[:40]}: {e}")
        return []


# ─────────────────────────────────────────────────────────────
# SEARCH DISPATCHER
# ─────────────────────────────────────────────────────────────

def _search(query: str, num: int = 5) -> list[dict]:
    """Try available search backends in priority order."""
    results = _search_google_cse(query, num)
    if results:
        return results

    results = _search_serper(query, num)
    if results:
        return results

    print(f"    [social_scraper] No search backend configured. Set GOOGLE_CSE_API_KEY+CX or SERPER_API_KEY in .env")
    return []


# ─────────────────────────────────────────────────────────────
# SIGNAL EXTRACTION
# ─────────────────────────────────────────────────────────────

def _clean_snippet(text: str) -> str:
    """Remove common junk from Google snippets."""
    # Remove date prefixes Google adds
    text = re.sub(r"^\w{3}\s+\d{1,2},\s+\d{4}\s*[—–-]\s*", "", text)
    # Remove "... " ellipsis starts
    text = text.strip().lstrip(".")
    # Remove trailing "..."
    text = text.rstrip(".")
    return text.strip()


def _extract_signals(
    results: list[dict],
    source_tag: str,
    base_signal: float = 2.0,
) -> list[dict[str, Any]]:
    """Convert search results into listener_brain-compatible signal dicts."""
    signals = []
    seen_texts = set()

    for r in results:
        title = r.get("title", "").strip()
        snippet = _clean_snippet(r.get("snippet", ""))

        # Title signal
        if title and title not in seen_texts and len(title) > 15:
            seen_texts.add(title)
            signals.append({
                "source": f"{source_tag}_title",
                "text": title[:300],
                "signal": base_signal + 1,
            })

        # Snippet signal (often contains caption text or post preview)
        if snippet and snippet not in seen_texts and len(snippet) > 30:
            seen_texts.add(snippet)
            # Higher signal for emotionally rich snippets
            boost = 0
            emotional_markers = [
                "i feel", "i've been", "no one told", "why do",
                "struggling", "finally", "diagnosed", "my doctor",
                "changed my life", "wish i knew", "nobody talks",
            ]
            snippet_lower = snippet.lower()
            for marker in emotional_markers:
                if marker in snippet_lower:
                    boost += 0.5
            signals.append({
                "source": f"{source_tag}_snippet",
                "text": snippet[:300],
                "signal": base_signal + min(boost, 3.0),
            })

    return signals


# ─────────────────────────────────────────────────────────────
# PUBLIC COLLECTORS
# ─────────────────────────────────────────────────────────────

def get_instagram_signals(custom_queries: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Collect Instagram trend signals via Google site:instagram.com search.

    Returns list of signal dicts compatible with listener_brain.py format.
    """
    queries = custom_queries or IG_QUERIES
    all_signals = []

    for query in queries:
        results = _search(query, num=5)
        signals = _extract_signals(results, source_tag="instagram_google", base_signal=2.0)
        all_signals.extend(signals)
        time.sleep(1.5)  # Rate limiting

    print(f"        {len(all_signals)} points from Instagram (via Google)")
    return all_signals


def get_linkedin_signals(custom_queries: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Collect LinkedIn trend signals via Google site:linkedin.com search.

    Returns list of signal dicts compatible with listener_brain.py format.
    """
    queries = custom_queries or LINKEDIN_QUERIES
    all_signals = []

    for query in queries:
        results = _search(query, num=5)
        signals = _extract_signals(results, source_tag="linkedin_google", base_signal=2.5)
        all_signals.extend(signals)
        time.sleep(1.5)

    print(f"        {len(all_signals)} points from LinkedIn (via Google)")
    return all_signals


# ─────────────────────────────────────────────────────────────
# INTEGRATION HELPER — drop-in for listener_brain.py
# ─────────────────────────────────────────────────────────────

def get_all_social_signals(
    ig_queries: list[str] | None = None,
    li_queries: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Collect from both platforms. Returns combined signal list.

    Usage in listener_brain.py run_listener():
        from social_scraper import get_all_social_signals
        social_data = get_all_social_signals()
        combined = wa_data + reddit_data + youtube_data + trends_data + social_data
    """
    ig = get_instagram_signals(ig_queries)
    li = get_linkedin_signals(li_queries)
    return ig + li


# ─────────────────────────────────────────────────────────────
# EVOLVING QUERIES — let AI suggest better searches next time
# ─────────────────────────────────────────────────────────────

def evolve_queries_from_intelligence(intel: dict[str, Any]) -> dict[str, list[str]]:
    """
    Given an intelligence.json dict, generate smarter IG/LinkedIn queries
    for the next collection cycle based on trending topics and keywords.

    Returns {"instagram": [...], "linkedin": [...]}.
    """
    topics = intel.get("trending_topics", [])[:5]
    keywords = intel.get("expanded_keywords", [])[:8]
    gaps = intel.get("content_gaps", [])[:3]

    ig_evolved = []
    li_evolved = []

    for topic in topics:
        # Clean topic for search query
        clean = topic.replace('"', "").strip()[:60]
        ig_evolved.append(f"site:instagram.com {clean}")
        li_evolved.append(f"site:linkedin.com {clean}")

    for kw in keywords[:5]:
        clean = kw.replace('"', "").strip()[:50]
        ig_evolved.append(f"site:instagram.com {clean}")
        li_evolved.append(f"site:linkedin.com {clean}")

    for gap in gaps:
        gap_text = gap.get("gap", gap) if isinstance(gap, dict) else str(gap)
        clean = gap_text.replace('"', "").strip()[:50]
        ig_evolved.append(f"site:instagram.com {clean}")

    return {
        "instagram": ig_evolved[:10],
        "linkedin": li_evolved[:10],
    }


# ─────────────────────────────────────────────────────────────
# STANDALONE TEST
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import os
    from dotenv import load_dotenv
    load_dotenv(override=True)

    GOOGLE_CSE_API_KEY = os.getenv("GOOGLE_CSE_API_KEY")
    GOOGLE_CSE_CX = os.getenv("GOOGLE_CSE_CX")
    SERPER_API_KEY = os.getenv("SERPER_API_KEY")

    print("\n  Social Scraper — Test Run")
    print("  " + "=" * 40)

    print("\n  [1/2] Instagram signals...")
    ig = get_instagram_signals()
    for s in ig[:5]:
        print(f"    [{s['source']}] (signal={s['signal']}) {s['text'][:80]}")

    print(f"\n  [2/2] LinkedIn signals...")
    li = get_linkedin_signals()
    for s in li[:5]:
        print(f"    [{s['source']}] (signal={s['signal']}) {s['text'][:80]}")

    print(f"\n  Total: {len(ig)} IG + {len(li)} LI = {len(ig) + len(li)} signals")
