"""
Content Generator
-----------------
Takes the latest intelligence run from SQLite and generates
ready-to-review social media drafts for all three brands.

Output per run:
  - 3 LinkedIn posts  (GMC, Endo Neutral, Harmanjeet)
  - 3 Instagram captions (GMC, Endo Neutral, Harmanjeet)
  - 1 WhatsApp community discussion prompt (expert group)

Usage:
  python content_generator.py              # uses latest run
  python content_generator.py --run-id 2  # uses specific run
"""

import os
import sys
import json
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR   = Path(__file__).parent
OUTPUT_DIR = BASE_DIR / "output" / "drafts"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
import database as db

client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# ─────────────────────────────────────────────────────────────
# BRAND DEFINITIONS
# ─────────────────────────────────────────────────────────────

BRANDS = {
    "gmc": {
        "name":  "Global Menopause Collective",
        "voice": (
            "Authoritative but warm. Use 'we' language — speak as a community, not an individual. "
            "Community-first framing. Clinical credibility without coldness. Never alarmist. "
            "Tone: a trusted older sister who happens to have medical knowledge and a global community behind her. "
            "The audience is women aged 35-65 navigating perimenopause, menopause, and hormonal health."
        ),
    },
    "endo_neutral": {
        "name":  "Endo Neutral",
        "voice": (
            "Philosophical, calm, measured. Speaks to the long game of hormonal health. "
            "Evidence-based but deeply human. Tone: a quiet authority — not reactive, not dramatic. "
            "Acknowledges complexity without creating fear. Treats the audience as intelligent adults "
            "who are tired of being either dismissed or panicked. "
            "The audience is women with endometriosis and complex hormonal conditions."
        ),
    },
    "harmanjeet": {
        "name":  "Harmanjeet Rekhi",
        "voice": (
            "Personal, visionary, first-person. Founder story voice. Bridges clinical and lived experience. "
            "Tone: a founder who has walked this path personally, who thinks in systems, and who is building "
            "something meaningful. Direct, warm, occasionally provocative. "
            "Uses 'I' language freely. Shares observations and insights, not just facts. "
            "The audience is women's health advocates, professionals, and community builders."
        ),
    },
}

# ─────────────────────────────────────────────────────────────
# LOAD INTELLIGENCE FROM SQLITE
# ─────────────────────────────────────────────────────────────

def load_run_intelligence(run_id: int) -> dict:
    conn = db.get_connection()

    run = conn.execute(
        "SELECT * FROM runs WHERE id=?", (run_id,)
    ).fetchone()

    topics = conn.execute(
        "SELECT topic FROM trending_topics WHERE run_id=? ORDER BY rank", (run_id,)
    ).fetchall()

    gaps = conn.execute(
        "SELECT gap, why_it_is_a_gap, emotional_signal FROM content_gaps WHERE run_id=?", (run_id,)
    ).fetchall()

    titles = conn.execute(
        "SELECT title FROM youtube_titles WHERE run_id=?", (run_id,)
    ).fetchall()

    keywords = conn.execute(
        "SELECT keyword FROM keywords WHERE run_id=? LIMIT 15", (run_id,)
    ).fetchall()

    conn.close()

    return {
        "run_id":   run_id,
        "timestamp": run["timestamp"],
        "trending_topics": [t["topic"] for t in topics],
        "content_gaps": [
            {
                "gap":             g["gap"],
                "why":             g["why_it_is_a_gap"] or "",
                "emotional_signal": g["emotional_signal"] or "",
            }
            for g in gaps
        ],
        "youtube_titles": [t["title"] for t in titles],
        "keywords":       [k["keyword"] for k in keywords],
    }


def get_latest_run_id() -> int:
    conn = db.get_connection()
    row = conn.execute("SELECT id FROM runs ORDER BY id DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        raise RuntimeError("No runs found in database. Run listener_brain.py first.")
    return row["id"]


# ─────────────────────────────────────────────────────────────
# AI CONTENT GENERATION — single call, all 7 pieces
# ─────────────────────────────────────────────────────────────

def generate_all_content(intel: dict) -> dict:
    topics_block = "\n".join(
        f"  {i+1}. {t}" for i, t in enumerate(intel["trending_topics"])
    )
    gaps_block = ""
    for i, g in enumerate(intel["content_gaps"], 1):
        gaps_block += f"  Gap {i}: {g['gap']}\n"
        if g["why"]:
            gaps_block += f"    Why it's a gap: {g['why'][:200]}\n"
        if g["emotional_signal"]:
            gaps_block += f"    Emotional signal: {g['emotional_signal']}\n"

    titles_block = "\n".join(f"  - {t}" for t in intel["youtube_titles"])
    keywords_block = ", ".join(intel["keywords"])

    brand_block = ""
    for key, b in BRANDS.items():
        brand_block += f"\n  {b['name']} ({key}):\n  {b['voice']}\n"

    prompt = f"""You are a specialist social media content writer for three women's health brands.
You have been given fresh intelligence data collected from Reddit, YouTube comments, and Google Trends.
Your job is to produce ready-to-review social media drafts that feel current, specific, and emotionally resonant — not generic.

=== INTELLIGENCE DATA (from this week's data collection) ===

TRENDING TOPICS:
{topics_block}

CONTENT GAPS (what women are desperate for but can't find):
{gaps_block}
HIGH-VALUE KEYWORDS: {keywords_block}

YOUTUBE TITLES PERFORMING WELL THIS WEEK:
{titles_block}

=== THREE BRANDS — DISTINCT VOICES ===
{brand_block}

=== YOUR TASK ===

Generate all 7 content pieces below. Each piece must:
- Draw directly from the intelligence data above — reference specific topics, not vague health generalities
- Match the brand voice exactly — a reader should immediately know which brand wrote it
- Be ready to post with only light editing (not a brief or a draft outline)
- Include suggested hashtags and a suggested best posting time

--- PIECE 1: LinkedIn post for Global Menopause Collective ---
Length: 150-200 words. Professional but warm. Can include a data point or clinical reference.
Hook in first line — must stop a scrolling professional.

--- PIECE 2: LinkedIn post for Endo Neutral ---
Length: 120-160 words. Measured, thoughtful. Opens with an observation or reframe, not a question.

--- PIECE 3: LinkedIn post for Harmanjeet Rekhi ---
Length: 100-150 words. First-person founder voice. One clear insight or provocation. Ends with an invitation to respond.

--- PIECE 4: Instagram caption for Global Menopause Collective ---
Length: 80-120 words. Warmer, more direct. First line is the hook (shown before 'more'). Community language.

--- PIECE 5: Instagram caption for Endo Neutral ---
Length: 60-90 words. Spare, considered. One idea explored well. Minimal hashtags — quality over quantity.

--- PIECE 6: Instagram caption for Harmanjeet Rekhi ---
Length: 70-100 words. Personal, direct. Can be a story fragment, an observation, or a behind-the-scenes moment.

--- PIECE 7: WhatsApp discussion prompt for the expert community ---
This goes to a private group of 70+ women's health experts (doctors, researchers, practitioners, advocates).
Length: 50-80 words. Not a social media post — a genuine discussion question that an expert would want to answer.
Tied to the most clinically interesting gap or trend in this week's data.
No hashtags. Conversational but substantive.

Return ONLY valid JSON in this exact structure — no text before or after:
{{
  "linkedin": {{
    "gmc":         {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}},
    "endo_neutral": {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}},
    "harmanjeet":  {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}}
  }},
  "instagram": {{
    "gmc":         {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}},
    "endo_neutral": {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}},
    "harmanjeet":  {{"post_text": "", "hashtags": "", "suggested_posting_time": ""}}
  }},
  "whatsapp": {{
    "prompt": ""
  }}
}}"""

    print("  Sending to Claude (claude-sonnet-4-6)...")
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4000,
        temperature=0.8,
        messages=[{"role": "user", "content": prompt}]
    )

    raw = response.content[0].text
    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        return json.loads(raw[start:end])


# ─────────────────────────────────────────────────────────────
# SAVE TO SQLITE
# ─────────────────────────────────────────────────────────────

def save_drafts_to_db(run_id: int, content: dict):
    now = str(datetime.now())
    conn = db.get_connection()
    with conn:
        for platform in ("linkedin", "instagram"):
            for brand_key, piece in content[platform].items():
                conn.execute(
                    """INSERT INTO content_drafts
                       (run_id, brand, platform, post_text, hashtags, status, created_at)
                       VALUES (?, ?, ?, ?, ?, 'draft', ?)""",
                    (
                        run_id,
                        brand_key,
                        platform,
                        piece.get("post_text", ""),
                        piece.get("hashtags", ""),
                        now,
                    )
                )
        conn.execute(
            """INSERT INTO content_drafts
               (run_id, brand, platform, post_text, hashtags, status, created_at)
               VALUES (?, 'all_experts', 'whatsapp', ?, '', 'draft', ?)""",
            (run_id, content["whatsapp"]["prompt"], now)
        )
    conn.close()


# ─────────────────────────────────────────────────────────────
# FORMAT + SAVE TEXT FILE
# ─────────────────────────────────────────────────────────────

def format_and_save(run_id: int, content: dict, intel: dict) -> Path:
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    out_path = OUTPUT_DIR / f"drafts_run{run_id}_{ts}.txt"

    lines = []
    lines.append("=" * 60)
    lines.append("  CONTENT DRAFTS")
    lines.append("=" * 60)
    lines.append(f"  Run ID    : {run_id}")
    lines.append(f"  Data from : {intel['timestamp'][:19]}")
    lines.append(f"  Generated : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"  Topics    : {intel['trending_topics'][0][:70]}...")
    lines.append("")

    def section(title):
        lines.append("-" * 60)
        lines.append(f"  {title}")
        lines.append("-" * 60)

    for platform in ("linkedin", "instagram"):
        label = "LINKEDIN" if platform == "linkedin" else "INSTAGRAM"
        for brand_key, brand_info in BRANDS.items():
            piece = content[platform][brand_key]
            section(f"{label} -- {brand_info['name'].upper()}")
            lines.append("")
            lines.append(piece.get("post_text", ""))
            lines.append("")
            if piece.get("hashtags"):
                lines.append(f"  Hashtags : {piece['hashtags']}")
            if piece.get("suggested_posting_time"):
                lines.append(f"  Post at  : {piece['suggested_posting_time']}")
            lines.append("")

    section("WHATSAPP -- EXPERT COMMUNITY DISCUSSION PROMPT")
    lines.append("")
    lines.append(content["whatsapp"]["prompt"])
    lines.append("")

    lines.append("=" * 60)
    lines.append("  STATUS: draft  |  Edit before posting")
    lines.append("=" * 60)

    report = "\n".join(lines)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)

    return out_path


# ─────────────────────────────────────────────────────────────
# PRINT TO CONSOLE
# ─────────────────────────────────────────────────────────────

def print_drafts(content: dict):
    for platform in ("linkedin", "instagram"):
        label = "LINKEDIN" if platform == "linkedin" else "INSTAGRAM"
        for brand_key, brand_info in BRANDS.items():
            piece = content[platform][brand_key]
            print(f"\n{'=' * 55}")
            print(f"  {label} -- {brand_info['name'].upper()}")
            print(f"{'=' * 55}")
            print(piece.get("post_text", ""))
            if piece.get("hashtags"):
                print(f"\n  Hashtags : {piece['hashtags']}")
            if piece.get("suggested_posting_time"):
                print(f"  Post at  : {piece['suggested_posting_time']}")

    print(f"\n{'=' * 55}")
    print("  WHATSAPP -- EXPERT COMMUNITY PROMPT")
    print(f"{'=' * 55}")
    print(content["whatsapp"]["prompt"])


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate brand content drafts from latest intelligence run")
    parser.add_argument("--run-id", type=int, default=None, help="Run ID to use (default: latest)")
    args = parser.parse_args()

    db.init_schema()

    run_id = args.run_id if args.run_id else get_latest_run_id()
    print(f"\n  Loading intelligence from run_id={run_id}...")

    intel = load_run_intelligence(run_id)
    print(f"  Topics: {len(intel['trending_topics'])} | Gaps: {len(intel['content_gaps'])} | Keywords: {len(intel['keywords'])}")

    print("\n" + "-" * 55)
    print("  Generating content drafts (7 pieces)...")
    print("-" * 55)

    content = generate_all_content(intel)

    save_drafts_to_db(run_id, content)
    out_path = format_and_save(run_id, content, intel)

    print_drafts(content)

    print(f"\n\n  Saved to SQLite content_drafts table.")
    print(f"  File: {out_path}")


if __name__ == "__main__":
    main()
