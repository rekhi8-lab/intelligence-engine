"""
Nervous System Layer
--------------------
Bridge between intelligence, performance, and production.

Reads from SQLite:
  - Last 2 intelligence runs (topics, gaps, titles, keywords, thumbnails)
  - Last 20 content_performance entries
  - Recent WhatsApp expert signals
  - Previous learning_signals (for continuity)

Sends ONE request to Claude Sonnet → strategic content guidance.

Outputs:
  - output/guidance/latest_brief.txt   (human-readable decision brief)
  - output/guidance/latest_brief.json  (machine-readable for future use)
  - learning_signals table in SQLite   (longitudinal learning store)

Runs inside GitHub Actions after content_generator.py.
Execution time: < 60 seconds. No new services required.
"""

import os
import sys
import re
import json
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR     = Path(__file__).parent
GUIDANCE_DIR = BASE_DIR / "output" / "guidance"
GUIDANCE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
import database as db

client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))


# ─────────────────────────────────────────────────────────────
# SCHEMA EXTENSION — learning_signals table
# ─────────────────────────────────────────────────────────────

def ensure_schema():
    conn = db.get_connection()
    with conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS learning_signals (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                winning_topics TEXT,
                losing_topics  TEXT,
                winning_hooks  TEXT,
                losing_hooks   TEXT,
                timestamp      DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
    conn.close()


# ─────────────────────────────────────────────────────────────
# STEP 1 — FETCH DATA
# ─────────────────────────────────────────────────────────────

def fetch_intelligence_data() -> dict:
    """Pull last 2 intelligence runs from SQLite."""
    conn = db.get_connection()

    runs = conn.execute(
        "SELECT id, timestamp FROM runs ORDER BY id DESC LIMIT 2"
    ).fetchall()

    if not runs:
        conn.close()
        return {}

    run_ids       = [r["id"] for r in runs]
    placeholders  = ",".join("?" * len(run_ids))

    topics = conn.execute(
        f"SELECT topic FROM trending_topics WHERE run_id IN ({placeholders}) ORDER BY run_id DESC, rank",
        run_ids
    ).fetchall()

    gaps = conn.execute(
        f"SELECT gap, why_it_is_a_gap FROM content_gaps WHERE run_id IN ({placeholders}) ORDER BY run_id DESC",
        run_ids
    ).fetchall()

    titles = conn.execute(
        f"SELECT title FROM youtube_titles WHERE run_id IN ({placeholders}) ORDER BY run_id DESC",
        run_ids
    ).fetchall()

    keywords = conn.execute(
        f"SELECT keyword FROM keywords WHERE run_id IN ({placeholders}) LIMIT 20",
        run_ids
    ).fetchall()

    thumbnails = conn.execute(
        f"SELECT text FROM thumbnail_ideas WHERE run_id IN ({placeholders}) ORDER BY run_id DESC LIMIT 10",
        run_ids
    ).fetchall()

    conn.close()

    def gap_str(g):
        base = g["gap"] or ""
        why  = g["why_it_is_a_gap"] or ""
        return f"{base} — {why}" if why else base

    return {
        "trending_topics": [t["topic"] for t in topics],
        "content_gaps":    [gap_str(g) for g in gaps],
        "youtube_titles":  [t["title"] for t in titles],
        "keywords":        [k["keyword"] for k in keywords],
        "thumbnail_ideas": [t["text"] for t in thumbnails],
    }


def fetch_performance_data() -> dict:
    """Last 20 performance entries, scored and ranked."""
    conn = db.get_connection()
    rows = conn.execute(
        """SELECT platform, brand, content_summary,
                  views, likes, comments, shares,
                  click_through_rate, notes
           FROM content_performance
           ORDER BY recorded_at DESC LIMIT 20"""
    ).fetchall()
    conn.close()

    if not rows:
        return {"top_performing": [], "low_performing": [], "platform_patterns": {}}

    scored = []
    for r in rows:
        # Weighted engagement score: shares > comments > likes > views
        score = (
            (r["views"]   or 0) * 0.1 +
            (r["likes"]   or 0) * 2   +
            (r["comments"] or 0) * 3  +
            (r["shares"]  or 0) * 4
        )
        scored.append({
            "platform": r["platform"] or "",
            "brand":    r["brand"]    or "",
            "summary":  (r["content_summary"] or "")[:150],
            "score":    score,
        })

    scored.sort(key=lambda x: x["score"], reverse=True)

    top = [f"{s['platform']} | {s['brand']} | {s['summary']}" for s in scored[:5] if s["score"] > 0]
    low = [f"{s['platform']} | {s['brand']} | {s['summary']}" for s in scored if s["score"] == 0][:5]

    platform_scores: dict[str, float] = {}
    for s in scored:
        p = s["platform"]
        platform_scores[p] = platform_scores.get(p, 0.0) + s["score"]

    return {
        "top_performing":    top,
        "low_performing":    low,
        "platform_patterns": platform_scores,
    }


def fetch_expert_signals() -> list[str]:
    """Recent WhatsApp expert signals for context."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT response FROM whatsapp_signals ORDER BY imported_at DESC LIMIT 10"
    ).fetchall()
    conn.close()
    return [(r["response"] or "")[:300] for r in rows]


def fetch_founder_signals() -> list[dict]:
    """Recent founder signals from personal inbox (last 20)."""
    conn = db.get_connection()
    try:
        rows = conn.execute(
            """SELECT source_type, topic, insight, emotional_signal, content_angle
               FROM founder_signals
               ORDER BY created_at DESC LIMIT 20"""
        ).fetchall()
        conn.close()
        return [
            {
                "source_type":      r["source_type"],
                "topic":            r["topic"] or "",
                "insight":          r["insight"] or "",
                "emotional_signal": r["emotional_signal"] or "",
                "content_angle":    r["content_angle"] or "",
            }
            for r in rows
        ]
    except Exception:
        conn.close()
        return []


def fetch_previous_learning() -> dict:
    """Most recent learning signal row for continuity."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM learning_signals ORDER BY id DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "previous_winning_topics": json.loads(row["winning_topics"] or "[]"),
        "previous_losing_topics":  json.loads(row["losing_topics"]  or "[]"),
        "previous_winning_hooks":  json.loads(row["winning_hooks"]  or "[]"),
        "previous_losing_hooks":   json.loads(row["losing_hooks"]   or "[]"),
    }


# ─────────────────────────────────────────────────────────────
# STEP 2 — ASSEMBLE CLAUDE INPUT PACKAGE
# ─────────────────────────────────────────────────────────────

def build_claude_input(intel: dict, perf: dict, expert_signals: list,
                       previous: dict, founder_signals: list) -> dict:
    return {
        "top_performing_topics":  perf.get("top_performing", []),
        "low_performing_topics":  perf.get("low_performing", []),
        "best_hooks":             intel.get("youtube_titles", [])[:6],
        "platform_patterns":      perf.get("platform_patterns", {}),
        "current_trends":         intel.get("trending_topics", []),
        "content_gaps":           intel.get("content_gaps", []),
        "user_intent_signals":    intel.get("keywords", []),
        "thumbnail_signals":      intel.get("thumbnail_ideas", []),
        "expert_signals":         expert_signals,
        "founder_signals":        founder_signals,   # personal inbox intelligence
        "previous_learning":      previous,
    }


# ─────────────────────────────────────────────────────────────
# STEP 3 — SINGLE CLAUDE CALL
# ─────────────────────────────────────────────────────────────

def call_claude(data: dict) -> dict:
    prompt = f"""You are the strategic intelligence advisor for a Women's Health content system.

Three brands publish content on this system:
- Global Menopause Collective (GMC) — authoritative, warm, community voice
- Endo Neutral — philosophical, evidence-based, calm authority
- Harmanjeet Rekhi — personal founder voice, first-person, visionary

You are given structured data from the intelligence layer. Your job is to make decisions.
Do NOT describe the data back. Do NOT hedge. Give clear, specific direction.

If performance data is sparse (system is early), lean harder on trend and expert signals.
If previous_learning exists, carry forward what was working — don't reset.

=== DATA PACKAGE ===
{json.dumps(data, indent=2, ensure_ascii=False)}

=== TASK ===

Return ONLY valid JSON, no text before or after:
{{
  "topic_guidance": "2-3 sentences. Which specific topics to prioritise this cycle and why. Name the topics explicitly.",
  "hook_guidance": "2-3 sentences. What emotional opening angles are working. What to avoid. Be specific — name patterns, not principles.",
  "title_guidance": "2-3 sentences. Which title structures and language patterns to use. Give a format example.",
  "thumbnail_guidance": "2-3 sentences. What text and visual direction to prioritise on thumbnails. Name specific phrases or visual approaches.",
  "format_guidance": "2-3 sentences. Content format recommendations — length, platform mix, short vs long-form balance.",
  "experiments": [
    "One specific untested angle, format, or topic to try this cycle. Be concrete — not a category, a specific piece idea."
  ],
  "learning_summary": {{
    "winning_topics": ["up to 4 specific topics that data shows are gaining traction"],
    "losing_topics":  ["up to 3 specific topics that are flat or not resonating"],
    "winning_hooks":  ["up to 3 specific hook patterns that are working"],
    "losing_hooks":   ["up to 2 hook patterns to retire"]
  }}
}}"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            temperature=0.4,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()

        # Strip markdown fences if Claude wraps in ```json
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()

        try:
            return json.loads(raw)
        except Exception:
            m = re.search(r"\{.*\}", raw, re.DOTALL)
            if m:
                return json.loads(m.group())
            raise ValueError("Could not extract valid JSON from Claude response")

    except Exception as e:
        print(f"  [NS] Claude error: {e}")
        return {}


# ─────────────────────────────────────────────────────────────
# STEP 4 — WRITE OUTPUT FILES
# ─────────────────────────────────────────────────────────────

def write_outputs(guidance: dict):
    ts  = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    ls  = guidance.get("learning_summary", {})

    def bullet_list(items: list, indent: str = "  → ") -> str:
        return "\n".join(f"{indent}{item}" for item in items) if items else f"{indent}(none logged yet)"

    lines = [
        "=" * 62,
        "  NERVOUS SYSTEM BRIEF",
        f"  Generated : {ts}",
        "=" * 62,
        "",
        "── TOPIC FOCUS ──────────────────────────────────────────────",
        guidance.get("topic_guidance", "N/A"),
        "",
        "── HOOK STRATEGY ────────────────────────────────────────────",
        guidance.get("hook_guidance", "N/A"),
        "",
        "── TITLE PATTERNS ───────────────────────────────────────────",
        guidance.get("title_guidance", "N/A"),
        "",
        "── THUMBNAIL DIRECTION ──────────────────────────────────────",
        guidance.get("thumbnail_guidance", "N/A"),
        "",
        "── FORMAT GUIDANCE ──────────────────────────────────────────",
        guidance.get("format_guidance", "N/A"),
        "",
        "── EXPERIMENT THIS CYCLE ────────────────────────────────────",
        bullet_list(guidance.get("experiments", [])),
        "",
        "── WHAT'S WINNING ───────────────────────────────────────────",
        f"  Topics : {' | '.join(ls.get('winning_topics', [])) or '(none yet)'}",
        f"  Hooks  : {' | '.join(ls.get('winning_hooks',  [])) or '(none yet)'}",
        "",
        "── WHAT'S LOSING ────────────────────────────────────────────",
        f"  Topics : {' | '.join(ls.get('losing_topics', [])) or '(none yet)'}",
        f"  Hooks  : {' | '.join(ls.get('losing_hooks',  [])) or '(none yet)'}",
        "",
        "=" * 62,
    ]

    brief_text = "\n".join(lines)

    txt_path  = GUIDANCE_DIR / "latest_brief.txt"
    json_path = GUIDANCE_DIR / "latest_brief.json"

    txt_path.write_text(brief_text, encoding="utf-8")
    json_path.write_text(
        json.dumps(guidance, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(brief_text)
    print(f"\n  [NS] Brief → {txt_path}")
    print(f"  [NS] JSON  → {json_path}")


# ─────────────────────────────────────────────────────────────
# STEP 5 — WRITE BACK LEARNING SIGNALS
# ─────────────────────────────────────────────────────────────

def save_learning_signals(guidance: dict):
    ls = guidance.get("learning_summary", {})
    if not any(ls.values()):
        print("  [NS] No learning signals to save.")
        return

    conn = db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO learning_signals
               (winning_topics, losing_topics, winning_hooks, losing_hooks)
               VALUES (?, ?, ?, ?)""",
            (
                json.dumps(ls.get("winning_topics", []), ensure_ascii=False),
                json.dumps(ls.get("losing_topics",  []), ensure_ascii=False),
                json.dumps(ls.get("winning_hooks",  []), ensure_ascii=False),
                json.dumps(ls.get("losing_hooks",   []), ensure_ascii=False),
            )
        )
    conn.close()
    print("  [NS] Learning signals written to DB.")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run_nervous_system():
    print("\n" + "=" * 62)
    print("  NERVOUS SYSTEM — synthesising intelligence...")
    print("=" * 62)

    # Ensure learning_signals table exists
    ensure_schema()

    # ── Step 1: Fetch all inputs ──────────────────────────────
    print("  [1/4] Fetching intelligence data...")
    intel = fetch_intelligence_data()
    if not intel:
        print("  [NS] No intelligence runs found in DB. Skipping.")
        return

    print("  [2/4] Fetching performance data...")
    perf = fetch_performance_data()

    print("  [3/4] Fetching expert signals, founder signals + previous learning...")
    expert_signals   = fetch_expert_signals()
    founder_signals  = fetch_founder_signals()
    previous         = fetch_previous_learning()

    # ── Step 2: Build Claude input ────────────────────────────
    claude_input = build_claude_input(intel, perf, expert_signals, previous, founder_signals)

    print(f"\n  Input summary:")
    print(f"    Trending topics  : {len(claude_input['current_trends'])}")
    print(f"    Content gaps     : {len(claude_input['content_gaps'])}")
    print(f"    Top performers   : {len(claude_input['top_performing_topics'])}")
    print(f"    Expert signals   : {len(claude_input['expert_signals'])}")
    print(f"    Founder signals  : {len(claude_input['founder_signals'])}")
    print(f"    Previous learning: {'yes' if previous else 'none yet (first run)'}")

    # ── Step 3: Claude synthesis ──────────────────────────────
    print("\n  [4/4] Running nervous system synthesis (Claude Sonnet)...")
    guidance = call_claude(claude_input)

    if not guidance:
        print("  [NS] Claude returned no guidance. Exiting gracefully.")
        return

    # ── Step 4: Write outputs ─────────────────────────────────
    write_outputs(guidance)

    # ── Step 5: Persist learning signals ─────────────────────
    save_learning_signals(guidance)

    print("\n  [NS] Nervous system complete.\n")


if __name__ == "__main__":
    run_nervous_system()
