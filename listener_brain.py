print("INTELLIGENCE ENGINE v3.0")

import os
import json
import time
import requests
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from googleapiclient.discovery import build
import anthropic

import query_engine as qe
import database as db
from social_scraper import evolve_queries_from_intelligence, get_all_social_signals

db.init_schema()

BASE_DIR = Path(__file__).parent


def _load_manual_keywords() -> list[str]:
    """Load owner-added keywords from inputs/manual/keywords.txt"""
    kw_path = BASE_DIR / "inputs" / "manual" / "keywords.txt"
    if not kw_path.is_file():
        return []
    lines = kw_path.read_text(encoding="utf-8").splitlines()
    kws = [l.strip() for l in lines if l.strip() and not l.startswith("#")]
    if kws:
        print(f"[listener] Loaded {len(kws)} manual keywords")
    return kws


# ─────────────────────────────────────────────────────────────
# CREDENTIALS
# ─────────────────────────────────────────────────────────────

load_dotenv(override=True)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
CLAUDE_API_KEY  = os.getenv("CLAUDE_API_KEY")

client  = anthropic.Anthropic(api_key=CLAUDE_API_KEY)
youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)

# ─────────────────────────────────────────────────────────────
# SOURCE 2 — YOUTUBE (evolved queries from query_engine)
# ─────────────────────────────────────────────────────────────

def get_youtube_data(queries: list[str]):
    data = []
    for query in queries:
        try:
            search = youtube.search().list(
                q=query,
                part="snippet",
                maxResults=5,
                type="video",
                order="relevance"
            ).execute()

            for item in search.get("items", []):
                video_id = item["id"]["videoId"]
                snippet  = item["snippet"]
                title    = snippet["title"]
                desc     = snippet.get("description", "")[:400]
                channel  = snippet.get("channelTitle", "")

                data.append({"source": "youtube_title",       "text": title,   "channel": channel, "signal": 3})
                if desc:
                    data.append({"source": "youtube_description", "text": desc, "signal": 1})

                # Comments — highest priority signal
                try:
                    comments = youtube.commentThreads().list(
                        part="snippet",
                        videoId=video_id,
                        maxResults=15,
                        order="relevance"
                    ).execute()

                    for c in comments.get("items", []):
                        top     = c["snippet"]["topLevelComment"]["snippet"]
                        comment = top["textDisplay"].strip()
                        likes   = top.get("likeCount", 0)
                        if len(comment) > 25:
                            data.append({
                                "source": "youtube_comment",
                                "text":   comment[:600],
                                "likes":  likes,
                                "signal": min(2 + likes * 0.1, 10)
                            })
                except Exception:
                    pass

        except Exception as e:
            print(f"    [YouTube] '{query[:40]}': {e}")

    print(f"        {len(data)} points from YouTube")
    return data


# ─────────────────────────────────────────────────────────────
# SOURCE 4 — GOOGLE TRENDS (optional, graceful fallback)
# ─────────────────────────────────────────────────────────────

def get_google_trends_data():
    try:
        from pytrends.request import TrendReq
        import time as _time

        batches = [
            ["menopause", "perimenopause", "PCOS",
             "endometriosis", "ADHD women"],
            ["hormonal imbalance", "early puberty girls",
             "HRT menopause", "brain fog menopause",
             "hot flashes"]
        ]
        data = []
        for i, batch in enumerate(batches):
            try:
                # Fresh instance per batch + pre-request delay
                if i > 0:
                    _time.sleep(15)  # 15s between batches
                pytrends = TrendReq(
                    hl="en-US", tz=0,
                    timeout=(10, 25),
                )
                pytrends.build_payload(
                    batch, timeframe="now 7-d", geo=""
                )
                related = pytrends.related_queries()
                for kw, results in related.items():
                    if not results:
                        continue
                    for kind in ("top", "rising"):
                        df = results.get(kind)
                        if df is not None and not df.empty:
                            for _, row in df.head(5).iterrows():
                                data.append({
                                    "source": f"google_trends_{kind}",
                                    "text": row["query"],
                                    "signal": 2
                                })
            except Exception as e:
                err = str(e)
                if "429" in err or "Too Many Requests" in err:
                    print(f"    [Trends batch {i+1}] Rate limited — skipping (retry next run)")
                else:
                    print(f"    [Trends batch {i+1}] {e}")
                _time.sleep(30)  # back off hard on any error

        print(f"        {len(data)} points from Google Trends")
        return data

    except ImportError:
        print("        [Trends] pytrends not installed")
        return []
    except Exception as e:
        print(f"        [Trends] {e}")
        return []



def build_data_sample(data: list[dict], n: int = 30) -> list[dict]:
    """
    Return top N raw data points by signal score so they are persisted
    in intelligence.json for transcript_analyzer and future reference.
    Prioritises YouTube comments and high-score Reddit posts.
    """
    ranked = sorted(data, key=lambda x: x.get("signal", 0), reverse=True)
    sample = []
    for item in ranked[:n]:
        sample.append({
            "source": item["source"],
            "text":   item["text"][:300]
        })
    return sample


# ─────────────────────────────────────────────────────────────
# JSON PARSER
# ─────────────────────────────────────────────────────────────

def _safe_json_parse(text: str) -> dict:
    import re

    # Strip markdown code fences
    text = text.strip()
    text = re.sub(r"^```json\s*", "", text)
    text = re.sub(r"^```\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    try:
        return json.loads(text)
    except Exception:
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except Exception:
            pass
        return {}


# ─────────────────────────────────────────────────────────────
# AI ANALYSIS — 12-field intelligence prompt
# ─────────────────────────────────────────────────────────────

def analyze_with_ai(data: list[dict]) -> dict:
    # Prioritise by signal score
    sorted_data = sorted(data, key=lambda x: x.get("signal", 0), reverse=True)

    # Bucket by source type
    whatsapp = [d for d in sorted_data if d["source"] == "whatsapp_expert"]
    comments = [d for d in sorted_data if d["source"] == "youtube_comment"]
    reddit   = [d for d in sorted_data if "reddit" in d["source"]]
    titles   = [d for d in sorted_data if d["source"] == "youtube_title"]
    trends   = [d for d in sorted_data if "trends" in d["source"]]

    sampled = whatsapp[:20] + comments[:40] + reddit[:35] + titles[:25] + trends[:15]

    formatted = []
    for item in sampled:
        src  = item["source"]
        text = item["text"].replace("\n", " ").strip()
        formatted.append(f"[{src}] {text}")

    data_block = "\n".join(formatted)

    prompt = f"""You are an elite content intelligence strategist specialising in women's health: menopause, perimenopause, PCOS, endometriosis, ADHD in women, early puberty, and hormonal health.

You are analysing REAL data collected from YouTube comments (highest priority), Reddit posts, Google search results, and Google Trends.

SIGNAL PRIORITY ORDER:
  1. WhatsApp expert signals — clinical and research insights from a curated expert community (highest weight)
  2. YouTube COMMENTS — raw emotional first-person pain. Look for: "I feel", "why do I", "no one told me", "my doctor said", "I've been struggling"
  3. Reddit posts — community lived experience and peer advice
  4. YouTube titles — what creators believe works
  5. Google search/Trends — search intent signals

━━━ RAW DATA ({len(sampled)} of {len(data)} total data points) ━━━
{data_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Produce a complete intelligence report with ALL 9 fields. Be specific, data-driven, psychologically deep. No generic advice.

FIELD SPECIFICATIONS:

1. trending_topics (10 items): Most repeated + emotionally charged topics. Return as a list of dicts, ranked by score descending. Each dict must have exactly these fields:
   - "key": the specific topic phrase (e.g. "perimenopause heart palpitations misdiagnosed as panic disorder")
   - "score": float 0.0–1.0 reflecting signal weight (volume + emotional intensity + recurrence)
   - "sources": int, number of raw signals (comments, posts, titles) that mention this topic
   - "sample": short verbatim quote from the raw data that best represents this topic (max 120 chars)

2. expanded_keywords (25–30 items): Long-tail pain keywords, question-based keywords, symptom combinations, underserved niche sub-topics. Examples: "why does my ADHD get worse before my period", "perimenopause brain fog affects my job performance".

3. content_gaps (3 items): Topics heavily discussed in comments/Reddit but barely covered on YouTube. State the specific gap and the emotional frustration driving it.

4. dominant_emotion (string): The single most prevalent emotional state driving the audience right now. Must be ONE word or short phrase from this list: seeking-answers, frustrated, hopeful, anxious, validated, isolated, empowered, grieving, curious, overwhelmed. Choose the one that best describes the dominant feeling in the scraped signals.

5. thumbnail_patterns (5 items): Visual/emotional thumbnail patterns inferred from titles and emotional language. E.g. "split-face before/after emotional state", "confrontational direct eye contact with bold symptom text".

6. thumbnail_text_ideas (10 items): 3–6 word high-conversion phrases. Curiosity-driven, emotionally triggering, topic-specific. Examples: "They Called It Anxiety", "My Doctor Was Wrong", "Nobody Warned Me This".

7. youtube_titles (10 items): Title formulas using emotional triggers + keyword clusters. Formats: "X Things...", "Why Your Doctor...", "I Finally Found Out...", "The Real Reason...", "Nobody Talks About..."

8. memory_keywords (20 items): Most important recurring terms from this dataset for longitudinal tracking.

9. next_search_queries (15 items): Deeper, more specific search queries for the next data collection cycle — based on what you found in this data. Should go further into sub-niches and emotional language discovered here.

Return ONLY valid JSON, no text before or after:
{{
  "trending_topics": [{"key": "", "score": 0.0, "sources": 0, "sample": ""}],
  "expanded_keywords": [],
  "content_gaps": [],
  "dominant_emotion": "",
  "thumbnail_patterns": [],
  "thumbnail_text_ideas": [],
  "youtube_titles": [],
  "memory_keywords": [],
  "next_search_queries": []
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            temperature=0.7,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text
        result = _safe_json_parse(raw)
        if not result:
            print("  [AI] Warning: could not parse JSON from AI response.")
            print(f"  [AI] Response start: {raw[:300]}")
            print(f"  [AI] Response end:   {raw[-200:]}")
        # Normalise trending_topics: coerce plain strings to scored dicts
        raw_tt = result.get("trending_topics", [])
        normed = []
        for item in raw_tt:
            if isinstance(item, dict):
                normed.append(item)
            elif isinstance(item, str):
                normed.append({"key": item, "score": 0.5, "sources": 1, "sample": ""})
        result["trending_topics"] = normed
        return result

    except Exception as e:
        print(f"  [AI] Error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# LEGACY MEMORY (kept for backward compat)
# ─────────────────────────────────────────────────────────────

def load_memory() -> dict:
    if os.path.exists("memory.json"):
        with open("memory.json", "r", encoding="utf-8") as f:
            return json.load(f)
    return {"keywords": [], "search_queries": []}


def save_memory(memory: dict):
    with open("memory.json", "w", encoding="utf-8") as f:
        json.dump(memory, f, indent=2, ensure_ascii=False)


def update_memory(memory: dict, ai_output: dict) -> dict:
    new_kw    = ai_output.get("memory_keywords", [])
    new_q     = ai_output.get("next_search_queries", [])
    memory["keywords"]       = list(set(memory.get("keywords", [])) | set(new_kw))
    memory["search_queries"] = list(set(memory.get("search_queries", [])) | set(new_q))
    memory["last_updated"]   = str(datetime.now())
    return memory


# ─────────────────────────────────────────────────────────────
# MAIN PIPELINE
# ─────────────────────────────────────────────────────────────

def run_listener():
    print("\n" + "-" * 52)
    print("  Collecting data from all sources...")
    print("-" * 52)

    # ── Load query pool (self-evolving) ───────────────────────
    pool = qe.load_pool()
    yt_queries = qe.select_queries(pool, n=12)
    _manual_kws = _load_manual_keywords()
    if _manual_kws:
        yt_queries = list(dict.fromkeys(yt_queries + _manual_kws))
    qe.print_pool_status(pool)
    print()

    # ── Collect from all sources ──────────────────────────────
    print("  [1/4] YouTube (evolved queries)...")
    youtube_data = get_youtube_data(yt_queries)

    print("  [2/4] Google Trends...")
    trends_data = get_google_trends_data()

    print("  [3/4] Instagram + LinkedIn + Reddit (via Google)...")
    # Load owner-added manual keywords for social search
    _manual_kws = _load_manual_keywords()
    _reddit_queries = (
        [f"site:reddit.com/r/Menopause {kw}"
         for kw in _manual_kws]
        if _manual_kws else None
    )
    _ig_queries = (
        [f"site:instagram.com {kw}"
         for kw in _manual_kws]
        if _manual_kws else None
    )
    _li_queries = (
        [f"site:linkedin.com {kw}"
         for kw in _manual_kws]
        if _manual_kws else None
    )
    social_data = get_all_social_signals(
        ig_queries=_ig_queries,
        li_queries=_li_queries,
        reddit_queries=_reddit_queries,
    )
    if _manual_kws:
        print(
            f"[listener] Injected {len(_manual_kws)} "
            f"manual keywords into social signals"
        )

    # ── WhatsApp expert signals (highest priority) ────────────
    print("  [4/4] WhatsApp expert signals...")
    wa_pending = db.get_pending_whatsapp_signals()
    wa_data = [
        {"source": "whatsapp_expert", "text": s["response"], "signal": 10}
        for s in wa_pending
    ]
    if wa_data:
        print(f"  [+] {len(wa_data)} WhatsApp expert signal(s) included")

    combined = wa_data + youtube_data + trends_data + social_data

    # Source breakdown
    source_counts: dict[str, int] = {}
    for d in combined:
        key = d["source"].split(":")[0]
        source_counts[key] = source_counts.get(key, 0) + 1

    print(f"\n  Total: {len(combined)} data points")
    for src, count in sorted(source_counts.items(), key=lambda x: -x[1]):
        print(f"    {src}: {count}")

    # ── AI analysis ───────────────────────────────────────────
    print("\n" + "-" * 52)
    print("  Running AI analysis (claude-sonnet-4-6)...")
    print("-" * 52)

    ai_output = analyze_with_ai(combined)

    if not ai_output:
        print("  [!] AI returned empty output - aborting.")
        return

    # ── Evolve query pool with AI output ─────────────────────
    pool = qe.evolve_pool(pool, ai_output, yt_queries)
    qe.save_pool(pool)
    print(f"\n  Query pool updated: {len(pool['active'])} active queries (run #{pool['run_count']})")

    # ── Update legacy memory ──────────────────────────────────
    memory = load_memory()
    memory = update_memory(memory, ai_output)
    save_memory(memory)

    # ── Save to SQLite (source of truth) ─────────────────────
    data_sources = {
        "youtube":       len(youtube_data),
        "google_trends": len(trends_data),
        "instagram":     len([s for s in social_data if "instagram" in s.get("source", "")]),
        "linkedin":      len([s for s in social_data if "linkedin" in s.get("source", "")]),
        "reddit_google": len([s for s in social_data if "reddit" in s.get("source", "")]),
        "total":         len(combined)
    }
    data_sample = build_data_sample(combined, n=40)

    run_id = db.save_run(ai_output, data_sources, yt_queries, engine_version="3.0")
    db.save_raw_signals(run_id, data_sample)
    if wa_pending:
        db.mark_whatsapp_signals_used([s["id"] for s in wa_pending], run_id)
        print(f"  {len(wa_pending)} WhatsApp signal(s) marked used (run_id={run_id})")
    print(f"\n  Saved to SQLite (run_id={run_id})")

    # ── Export intelligence.json (backward compat for transcript_analyzer) ──
    intelligence = {
        "timestamp": str(datetime.now()),
        "engine_version": "3.0",
        **ai_output,
        "dominant_emotion": ai_output.get(
            "dominant_emotion", "not analysed"
        ),
        "data_sources": data_sources,
        "query_pool": {
            "run":          pool["run_count"],
            "active":       len(pool["active"]),
            "retired":      len(pool["retired"]),
            "used_this_run": yt_queries
        },
        "data_sample": data_sample
    }

    evolved_social = evolve_queries_from_intelligence(ai_output)
    intelligence["evolved_social_queries"] = evolved_social

    with open("intelligence.json", "w", encoding="utf-8") as f:
        json.dump(intelligence, f, indent=2, ensure_ascii=False)

    print("  Intelligence exported to intelligence.json")
    print(f"  Trending topics:     {len(ai_output.get('trending_topics', []))}")
    print(f"  Expanded keywords:   {len(ai_output.get('expanded_keywords', []))}")
    print(f"  YouTube titles:      {len(ai_output.get('youtube_titles', []))}")
    print(f"  Thumbnail ideas:     {len(ai_output.get('thumbnail_text_ideas', []))}")
    print(f"  Next search queries: {len(ai_output.get('next_search_queries', []))}")
    print(f"  Data sample stored:  {len(data_sample)} points")


# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    run_listener()
