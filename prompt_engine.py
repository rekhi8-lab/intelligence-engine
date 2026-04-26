"""
Prompt Engine
-------------
Runs after every pipeline cycle. Checks what data is missing or stale,
then writes calm, actionable guidance files to Google Drive.

This is NOT a notification system. It writes two files:
  output/prompts/system_requests.txt  — what the system needs and why
  output/prompts/priority.txt         — prioritised action list

Design principles:
  - Never repeat the same prompt within 4 days (approx 2 pipeline runs)
  - Explain WHY each input matters, not just what's needed
  - Calm tone — a helpful assistant, not an alarm
  - Guides the user to the exact Drive folder to use
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR      = Path(__file__).parent
PROMPTS_DIR   = BASE_DIR / "output" / "prompts"
PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

DRIVE_ROOT    = "https://drive.google.com/drive/folders/1tnU4bjFcLVdXx57yARwU__PVVvWoWPD1"
REPEAT_WINDOW = timedelta(days=4)   # don't repeat same prompt within ~2 runs

sys.path.insert(0, str(BASE_DIR))
import database as db


# ─────────────────────────────────────────────────────────────
# SCHEMA
# ─────────────────────────────────────────────────────────────

def ensure_schema():
    db.init_schema()


# ─────────────────────────────────────────────────────────────
# PROMPT DEDUPLICATION
# ─────────────────────────────────────────────────────────────

def was_shown_recently(prompt_key: str) -> bool:
    """Return True if this prompt was already shown within REPEAT_WINDOW."""
    conn = db.get_connection()
    cutoff = (datetime.now() - REPEAT_WINDOW).isoformat()
    row = conn.execute(
        "SELECT id FROM prompt_log WHERE prompt_key = ? AND shown_at > ? LIMIT 1",
        (prompt_key, cutoff)
    ).fetchone()
    conn.close()
    return row is not None


def log_prompt(prompt_key: str):
    conn = db.get_connection()
    with conn:
        conn.execute(
            "INSERT INTO prompt_log (prompt_key) VALUES (?)", (prompt_key,)
        )
    conn.close()


# ─────────────────────────────────────────────────────────────
# FRESHNESS CHECKS
# ─────────────────────────────────────────────────────────────

def days_since(iso_timestamp: str) -> float:
    """Return days elapsed since an ISO timestamp string."""
    try:
        dt = datetime.fromisoformat(iso_timestamp[:19])
        return (datetime.now() - dt).total_seconds() / 86400
    except Exception:
        return 999.0


def check_whatsapp_expert_signals() -> dict | None:
    """HIGH — expert signals missing if no import in 5 days."""
    key = "whatsapp_expert_missing"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT imported_at FROM whatsapp_signals ORDER BY imported_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or days_since(row["imported_at"]) > 5:
        return {
            "key":      key,
            "priority": "HIGH",
            "title":    "WhatsApp Expert Signals Needed",
            "body": (
                "Your 70-member expert community hasn't contributed new signals in 5+ days.\n\n"
                "Action:\n"
                "  1. Run this on your laptop:\n"
                "       python whatsapp_import.py generate\n"
                "  2. Copy the generated prompt and send it to your expert WhatsApp group\n"
                "  3. When responses come in, save them to a text file and run:\n"
                "       python whatsapp_import.py import responses.txt\n\n"
                "Why this matters: Expert signals are weighted highest in the intelligence\n"
                "engine. They provide clinical authority that YouTube comments can't match."
            ),
        }
    return None


def check_performance_data() -> dict | None:
    """HIGH — no performance logging in 7 days."""
    key = "performance_missing"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT recorded_at FROM content_performance ORDER BY recorded_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or days_since(row["recorded_at"]) > 7:
        return {
            "key":      key,
            "priority": "HIGH",
            "title":    "Performance Data Needed",
            "body": (
                "No content performance has been logged in the last 7 days.\n\n"
                "The nervous system already reads this data — the more you log,\n"
                "the more precisely it can guide which topics and hooks to prioritise.\n\n"
                "Action — run on your laptop:\n"
                "  python auditor.py log\n\n"
                "It will ask you for: platform, brand, content summary, views, likes,\n"
                "comments, shares. Even rough numbers improve the system significantly.\n\n"
                "For YouTube specifically:\n"
                "  python auditor.py youtube https://youtu.be/YOUR_VIDEO_URL"
            ),
        }
    return None


def check_personal_inbox() -> dict | None:
    """MEDIUM — personal inbox not updated in 5 days."""
    key = "personal_inbox_missing"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT created_at FROM founder_signals ORDER BY created_at DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or days_since(row["created_at"]) > 5:
        return {
            "key":      key,
            "priority": "MEDIUM",
            "title":    "Personal Inbox — Upload Your Curated Signals",
            "body": (
                "No founder signals have been processed recently. Your personal\n"
                "WhatsApp group (where you save articles, URLs, screenshots, insights)\n"
                "is a high-quality proprietary signal source the system can't get elsewhere.\n\n"
                "Action:\n"
                "  1. Open WhatsApp on your phone\n"
                "  2. Open your personal saved-content group\n"
                "  3. Tap the group name → Export Chat → Include Media\n"
                "  4. Save the zip file, extract it\n"
                "  5. Upload to Google Drive:\n"
                f"     {DRIVE_ROOT}\n"
                "     → inputs → whatsapp → personal_inbox\n"
                "     Upload: chat.txt + any .jpg/.png files\n\n"
                "The system will automatically process them on the next pipeline run.\n\n"
                "What to include: URLs to articles, screenshots of research, text insights,\n"
                "anything you've been saving because it felt relevant to your content."
            ),
        }
    return None


def check_youtube_sources() -> dict | None:
    """LOW — no YouTube sources tracked yet, or none added in 14 days."""
    key = "youtube_sources_expand"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT added_on FROM tracked_sources WHERE platform='youtube' ORDER BY added_on DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row or days_since(row["added_on"]) > 14:
        return {
            "key":      key,
            "priority": "LOW",
            "title":    "Expand YouTube Sources",
            "body": (
                "Expanding your tracked YouTube sources increases the intelligence\n"
                "engine's signal diversity. Right now it searches by topic keywords.\n"
                "Adding specific channels gives you consistent competitor/inspiration data.\n\n"
                "Channels worth adding:\n"
                "  - Your own channel (to track your own performance)\n"
                "  - Top competitors in women's health / menopause / ADHD space\n"
                "  - Channels you find inspiring or study for format ideas\n\n"
                "Action — run on your laptop:\n"
                "  python prompt_engine.py add-source youtube \"Channel Name\" "
                "https://youtube.com/@handle own/competitor/inspiration\n\n"
                "Example:\n"
                "  python prompt_engine.py add-source youtube \"Dr. Mary Claire Haver\" "
                "https://youtube.com/@DrMaryClaire competitor"
            ),
        }
    return None


def check_instagram_sources() -> dict | None:
    """LOW — no Instagram sources tracked."""
    key = "instagram_sources_missing"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM tracked_sources WHERE platform='instagram'"
    ).fetchone()["c"]
    conn.close()
    if count == 0:
        return {
            "key":      key,
            "priority": "LOW",
            "title":    "Add Instagram Sources",
            "body": (
                "No Instagram accounts are being tracked. Adding accounts you\n"
                "want to monitor (for caption styles, hooks, content formats)\n"
                "builds a reference library for future content generation.\n\n"
                "Action — run on your laptop:\n"
                "  python prompt_engine.py add-source instagram \"@handle\" "
                "https://instagram.com/handle own/inspiration\n\n"
                "Start with: your own accounts + 3–5 accounts whose format\n"
                "you admire in the women's health space."
            ),
        }
    return None


def check_linkedin_sources() -> dict | None:
    """LOW — no LinkedIn sources tracked."""
    key = "linkedin_sources_missing"
    if was_shown_recently(key):
        return None
    conn = db.get_connection()
    count = conn.execute(
        "SELECT COUNT(*) as c FROM tracked_sources WHERE platform='linkedin'"
    ).fetchone()["c"]
    conn.close()
    if count == 0:
        return {
            "key":      key,
            "priority": "LOW",
            "title":    "Add LinkedIn Sources",
            "body": (
                "No LinkedIn profiles or communities are being tracked.\n\n"
                "LinkedIn is important for the GMC and Harmanjeet Rekhi brands —\n"
                "tracking how peers and thought leaders write posts in this space\n"
                "gives the content generator better reference patterns.\n\n"
                "Action — run on your laptop:\n"
                "  python prompt_engine.py add-source linkedin \"Name\" "
                "https://linkedin.com/in/handle own/community/competitor\n\n"
                "Start with: your own profile + 3 thought leaders in women's health\n"
                "whose LinkedIn writing style you want the system to learn from."
            ),
        }
    return None


# ─────────────────────────────────────────────────────────────
# COLLECT ALL PROMPTS
# ─────────────────────────────────────────────────────────────

def collect_prompts() -> list[dict]:
    checks = [
        check_whatsapp_expert_signals,
        check_performance_data,
        check_personal_inbox,
        check_youtube_sources,
        check_instagram_sources,
        check_linkedin_sources,
    ]
    prompts = []
    for check in checks:
        result = check()
        if result:
            prompts.append(result)
    return prompts


# ─────────────────────────────────────────────────────────────
# WRITE OUTPUT FILES
# ─────────────────────────────────────────────────────────────

def write_outputs(prompts: list[dict]):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

    # ── system_requests.txt ───────────────────────────────────
    high   = [p for p in prompts if p["priority"] == "HIGH"]
    medium = [p for p in prompts if p["priority"] == "MEDIUM"]
    low    = [p for p in prompts if p["priority"] == "LOW"]

    req_lines = [
        "=" * 62,
        "  SYSTEM REQUESTS",
        f"  Generated : {ts}",
        f"  Open items: {len(prompts)} ({len(high)} high / {len(medium)} medium / {len(low)} low)",
        "=" * 62,
    ]

    for section_label, section_prompts in [
        ("🔴  HIGH PRIORITY", high),
        ("🟡  MEDIUM PRIORITY", medium),
        ("🟢  LOW PRIORITY", low),
    ]:
        if not section_prompts:
            continue
        req_lines += ["", f"── {section_label} " + "─" * (50 - len(section_label))]
        for p in section_prompts:
            req_lines += [
                "",
                f"  ▸ {p['title']}",
                "  " + "─" * 58,
            ]
            for line in p["body"].splitlines():
                req_lines.append(f"  {line}")
            req_lines.append("")

    if not prompts:
        req_lines += [
            "",
            "  ✓ All systems have sufficient data.",
            "  No action needed from you this cycle.",
            "",
        ]

    req_lines.append("=" * 62)
    req_text = "\n".join(req_lines)

    # ── priority.txt ──────────────────────────────────────────
    pri_lines = [
        "=" * 62,
        "  ACTION PRIORITY LIST",
        f"  Generated : {ts}",
        "=" * 62,
        "",
    ]
    if prompts:
        for i, p in enumerate(sorted(prompts, key=lambda x: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}[x["priority"]]), 1):
            symbol = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}[p["priority"]]
            pri_lines.append(f"  {i}. [{p['priority']}] {symbol}  {p['title']}")
        pri_lines += [
            "",
            "  → Full guidance for each item is in system_requests.txt",
        ]
    else:
        pri_lines.append("  ✓ Nothing needed this cycle.")

    pri_lines.append("")
    pri_lines.append("=" * 62)
    pri_text = "\n".join(pri_lines)

    # Write files
    req_path = PROMPTS_DIR / "system_requests.txt"
    pri_path = PROMPTS_DIR / "priority.txt"

    req_path.write_text(req_text, encoding="utf-8")
    pri_path.write_text(pri_text, encoding="utf-8")

    print(req_text)
    print(f"\n  [PE] system_requests.txt → {req_path}")
    print(f"  [PE] priority.txt        → {pri_path}")


# ─────────────────────────────────────────────────────────────
# ADD SOURCE (CLI helper)
# ─────────────────────────────────────────────────────────────

def cmd_add_source(args: list[str]):
    """CLI: python prompt_engine.py add-source <platform> <name> <url_or_handle> <type>"""
    if len(args) < 4:
        print("Usage: python prompt_engine.py add-source <platform> <name> <url_or_handle> <type>")
        print("  platform : youtube | instagram | linkedin | whatsapp")
        print("  type     : own | competitor | inspiration | community")
        return
    platform    = args[0].lower()
    source_name = args[1]
    handle      = args[2]
    source_type = args[3].lower()

    conn = db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO tracked_sources (platform, source_name, source_type)
               VALUES (?, ?, ?)""",
            (platform, f"{source_name} ({handle})", source_type)
        )
    conn.close()
    print(f"  [PE] Added: [{platform}] {source_name} ({handle}) — type: {source_type}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run_prompt_engine():
    print("\n" + "=" * 62)
    print("  PROMPT ENGINE — checking data freshness...")
    print("=" * 62)

    ensure_schema()

    prompts = collect_prompts()

    print(f"\n  Checks complete: {len(prompts)} item(s) need attention")

    write_outputs(prompts)

    # Log all prompts shown this run
    for p in prompts:
        log_prompt(p["key"])

    print(f"\n  [PE] Prompt engine complete.\n")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) > 1 and _sys.argv[1] == "add-source":
        db.init_schema()
        cmd_add_source(_sys.argv[2:])
    else:
        run_prompt_engine()
