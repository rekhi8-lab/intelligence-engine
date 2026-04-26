"""
Source Manager
--------------
Cloud-native source intelligence layer.

Reads the "Intelligence Engine — Sources" Google Sheet every pipeline run:
  - Syncs new channels / accounts to the tracked_sources DB
  - For YouTube sources:
      mine        → fetches recent videos, analyses performance patterns,
                    suggests improvements
      competitor  → extracts what's working, new content directions to adopt
      inspiration → identifies unique angles to adapt for our three brands
  - For Instagram / LinkedIn sources:
      No public API — records source in DB; user uploads screenshots/exports
      to Drive and personal_inbox_parser.py handles those
  - Processes "New Inputs" tab: unprocessed rows go to inputs/manual/insights.txt
    so personal_inbox_parser picks them up next; marks them Processed in the sheet
  - Writes a Pipeline Status row back to the sheet after every run
  - Saves channel analysis to DB for nervous_system to use

Run order in GitHub Actions:
  after nervous_system.py, before personal_inbox_parser.py

Auth:
  Service account (GDRIVE_SERVICE_ACCOUNT_JSON) — must be Editor on the sheet.
  YouTube API key (YOUTUBE_API_KEY).
"""

import os
import sys
import json
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR    = Path(__file__).parent
MANUAL_DIR  = BASE_DIR / "inputs" / "manual"
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
INSIGHTS_FILE = MANUAL_DIR / "insights.txt"

sys.path.insert(0, str(BASE_DIR))
import database as db

client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))


# ── Auth ──────────────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]


def get_sheets_service():
    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        local = BASE_DIR / "service_account.json"
        if local.exists():
            sa_json = local.read_text()
        else:
            print("  [SM] GDRIVE_SERVICE_ACCOUNT_JSON not set — skipping source sync.")
            return None

    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    info  = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def get_sheet_id() -> str | None:
    """Resolve SOURCES_SHEET_ID from env, .env, or .sources_sheet_id file."""
    sid = os.environ.get("SOURCES_SHEET_ID", "").strip()
    if sid:
        return sid
    local = BASE_DIR / ".sources_sheet_id"
    if local.exists():
        return local.read_text().strip()
    return None


# ── Sheet read helpers ────────────────────────────────────────────────────────

def read_tab(service, sheet_id: str, tab_name: str) -> list[list[str]]:
    """Return all rows from a sheet tab (including header)."""
    try:
        res = service.spreadsheets().values().get(
            spreadsheetId=sheet_id,
            range=f"'{tab_name}'!A:Z"
        ).execute()
        return res.get("values", [])
    except Exception as e:
        print(f"  [SM] Cannot read tab '{tab_name}': {e}")
        return []


def write_tab_row(service, sheet_id: str, tab_name: str, values: list):
    """Append a row to the end of a sheet tab."""
    try:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"'{tab_name}'!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [values]}
        ).execute()
    except Exception as e:
        print(f"  [SM] Cannot append to tab '{tab_name}': {e}")


def update_cell(service, sheet_id: str, tab_name: str, row: int, col: int, value: str):
    """Update a single cell (1-indexed row and col)."""
    col_letter = chr(ord("A") + col - 1)
    cell_ref   = f"'{tab_name}'!{col_letter}{row}"
    try:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=cell_ref,
            valueInputOption="RAW",
            body={"values": [[value]]}
        ).execute()
    except Exception as e:
        print(f"  [SM] Cannot update cell {cell_ref}: {e}")


# ── DB helpers ────────────────────────────────────────────────────────────────

def ensure_schema():
    """Add any missing columns to tracked_sources (migration for existing DBs)."""
    conn = db.get_connection()
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(tracked_sources)").fetchall()
    }
    migrations = {
        "source_url":    "ALTER TABLE tracked_sources ADD COLUMN source_url    TEXT",
        "channel_id":    "ALTER TABLE tracked_sources ADD COLUMN channel_id    TEXT",
        "last_analyzed": "ALTER TABLE tracked_sources ADD COLUMN last_analyzed DATETIME",
        "notes":         "ALTER TABLE tracked_sources ADD COLUMN notes         TEXT",
    }
    for col, sql in migrations.items():
        if col not in existing:
            try:
                conn.execute(sql)
                conn.commit()
            except Exception:
                pass
    conn.close()


def upsert_source(platform: str, name: str, url: str,
                  source_type: str, channel_id: str | None = None) -> int:
    """Insert or update a tracked_source row. Returns the row ID."""
    conn = db.get_connection()
    existing = conn.execute(
        "SELECT id FROM tracked_sources WHERE platform=? AND source_name=?",
        (platform, name)
    ).fetchone()

    with conn:
        if existing:
            conn.execute(
                """UPDATE tracked_sources
                   SET source_url=?, source_type=?, channel_id=COALESCE(?, channel_id)
                   WHERE id=?""",
                (url, source_type, channel_id, existing["id"])
            )
            row_id = existing["id"]
        else:
            cur = conn.execute(
                """INSERT INTO tracked_sources (platform, source_name, source_url, source_type, channel_id)
                   VALUES (?, ?, ?, ?, ?)""",
                (platform, name, url, source_type, channel_id)
            )
            row_id = cur.lastrowid
    conn.close()
    return row_id


def save_channel_analysis(source_id: int, analysis: dict, video_count: int):
    conn = db.get_connection()
    ls   = analysis.get("learning_signals", {})
    with conn:
        conn.execute(
            """INSERT INTO channel_analysis
               (source_id, videos_analyzed, top_topics, top_hooks, format_patterns,
                strategic_directions, raw_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source_id,
                video_count,
                json.dumps(ls.get("top_topics", []),     ensure_ascii=False),
                json.dumps(ls.get("top_hooks",  []),     ensure_ascii=False),
                json.dumps(ls.get("format_patterns", []), ensure_ascii=False),
                analysis.get("strategic_directions", ""),
                json.dumps(analysis, ensure_ascii=False),
            )
        )
        conn.execute(
            "UPDATE tracked_sources SET last_analyzed=?, last_used=? WHERE id=?",
            (datetime.now().isoformat(), datetime.now().isoformat(), source_id)
        )
    conn.close()


# ── YouTube analysis ──────────────────────────────────────────────────────────

def analyse_youtube_channel(name: str, url: str,
                              source_type: str, source_id: int) -> dict | None:
    """
    Fetch recent videos and run Claude analysis tailored to source_type:
      mine        → what's working, what to improve, post frequency
      competitor  → what's resonating, gaps we can exploit, topics to add
      inspiration → unique angles, format ideas, hooks to adapt
    """
    from youtube_scraper import resolve_channel_id, get_channel_videos, get_channel_stats

    print(f"    → Resolving channel ID for: {name}")
    channel_id = resolve_channel_id(url or name)
    if not channel_id:
        print(f"    [skip] Could not resolve channel ID for '{name}'")
        return None

    # Cache channel_id in DB
    conn = db.get_connection()
    with conn:
        conn.execute(
            "UPDATE tracked_sources SET channel_id=? WHERE id=?",
            (channel_id, source_id)
        )
    conn.close()

    print(f"    → Fetching videos (channel: {channel_id})")
    videos = get_channel_videos(channel_id, max_results=10)
    stats  = get_channel_stats(channel_id)

    if not videos:
        print(f"    [skip] No videos returned for '{name}'")
        return None

    # Build a compact video list for Claude
    video_lines = []
    for v in videos[:10]:
        video_lines.append(
            f"  Title: {v['title']}\n"
            f"  Views: {v['views']:,}  Likes: {v['likes']:,}  Comments: {v['comments']:,}\n"
            f"  Published: {v['published_at'][:10]}\n"
            f"  Description: {v['description'][:150]}\n"
        )
    video_block = "\n".join(video_lines)

    type_instructions = {
        "mine": """
This is OUR OWN channel. Analyse:
1. What topics and formats are getting the most engagement (views, likes, comments)?
2. What is the content posting frequency and consistency?
3. What title/hook patterns are performing well vs poorly?
4. What should we do MORE of, and what should we change?
5. Identify any content gaps — topics the audience is likely interested in that we haven't covered.
""",
        "competitor": """
This is a COMPETITOR channel. Analyse:
1. What topics are getting the highest engagement — what can we learn?
2. What hooks and title patterns are resonating with the audience?
3. What format patterns (long-form, shorts, series) are working for them?
4. What content gaps do THEY have that WE can fill?
5. What unique angles are they missing that we could own?
""",
        "inspiration": """
This is an INSPIRATION channel (different niche, but has useful format/content ideas). Analyse:
1. What formats, hooks, or structures could we adapt for women's health content?
2. What emotional triggers or language patterns are getting engagement?
3. What series or content structures could we borrow and adapt?
4. What is uniquely different about their approach that we should consider?
""",
    }

    instructions = type_instructions.get(source_type.lower(), type_instructions["competitor"])

    prompt = f"""You are analysing a YouTube channel for a Women's Health content intelligence system.

Three brands publish on this system:
- Global Menopause Collective (GMC) — authoritative, warm, community voice
- Endo Neutral — philosophical, evidence-based, calm authority
- Harmanjeet Rekhi — personal founder voice, first-person, visionary

Channel: {name}
Type: {source_type}
Subscribers: {stats.get('subscribers', 'unknown'):,}
Total views: {stats.get('total_views', 'unknown'):,}

Recent videos (sorted by view count, highest first):
{video_block}

{instructions}

Return ONLY valid JSON, no text before or after:
{{
  "channel_summary": "1-2 sentences describing what this channel does and its current trajectory",
  "top_performing_topics": ["topic 1", "topic 2", "topic 3"],
  "top_performing_hooks":  ["hook pattern 1", "hook pattern 2"],
  "format_patterns": ["format observation 1", "format observation 2"],
  "strategic_directions": "2-3 specific actionable directions for our brands based on this channel",
  "learning_signals": {{
    "top_topics":      ["up to 4 specific topics gaining traction on this channel"],
    "top_hooks":       ["up to 3 hook patterns that are working"],
    "format_patterns": ["up to 3 format patterns worth adopting"]
  }},
  "gaps_to_exploit": ["gap 1", "gap 2"]
}}"""

    try:
        response = client.messages.create(
            model="claude-haiku-4-5",
            max_tokens=1200,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )
        raw = response.content[0].text.strip()
        raw = re.sub(r"```(?:json)?\s*", "", raw).strip()
        result = json.loads(raw)
        result["channel_name"] = name
        result["source_type"]  = source_type
        result["videos_count"] = len(videos)
        return result

    except Exception as e:
        print(f"    [SM] Claude error for {name}: {e}")
        return None


# ── New Inputs processing ────────────────────────────────────────────────────

def process_new_inputs(service, sheet_id: str) -> int:
    """
    Read unprocessed rows from the New Inputs tab and write them to
    inputs/manual/insights.txt for personal_inbox_parser to pick up.
    Marks each processed row in the sheet.
    Returns count of items processed.
    """
    rows = read_tab(service, sheet_id, "New Inputs")
    if len(rows) < 2:
        return 0  # only header or empty

    processed = 0
    for i, row in enumerate(rows[1:], start=2):  # row index is 1-based in Sheets
        if len(row) < 1:
            continue
        content    = (row[0] or "").strip()
        is_done    = (row[2] if len(row) > 2 else "").strip().lower()

        if not content or is_done in ("yes", "✓", "done"):
            continue

        # Append to insights file
        ts_line = f"\n\n--- Submitted via Sources Sheet {datetime.now().strftime('%Y-%m-%d %H:%M')} ---\n"
        with open(INSIGHTS_FILE, "a", encoding="utf-8") as f:
            f.write(ts_line + content + "\n")

        # Mark as Processed in the sheet (column C = col 3)
        update_cell(service, sheet_id, "New Inputs", i, 3, "Yes")
        processed += 1

    return processed


# ── Source tab parsing ────────────────────────────────────────────────────────

def parse_source_rows(rows: list[list[str]], platform: str) -> list[dict]:
    """
    Parse rows from a source tab into a list of source dicts.
    Skips header row. Handles missing columns gracefully.
    row_index is the 1-based Sheets row number (row 1 = header, row 2 = first data row).
    """
    if len(rows) < 2:
        return []

    sources = []
    for sheets_row, row in enumerate(rows[1:], start=2):  # row 2 is first data row in Sheets
        if len(row) < 1:
            continue
        name   = (row[0] if len(row) > 0 else "").strip()
        url    = (row[1] if len(row) > 1 else "").strip()
        stype  = (row[2] if len(row) > 2 else "competitor").strip().lower()
        status = (row[4] if len(row) > 4 else "active").strip().lower()

        if not name or status in ("paused", "inactive", "skip"):
            continue

        # Normalise type
        if stype not in ("mine", "competitor", "inspiration"):
            stype = "competitor"

        sources.append({
            "name":        name,
            "url":         url,
            "source_type": stype,
            "platform":    platform,
            "row_index":   sheets_row,   # exact 1-based row number in Sheets
        })

    return sources


# ── Tab creation helper ───────────────────────────────────────────────────────

def ensure_linked_sources_tab(service, sheet_id: str):
    """Create the 'Linked Sources' tab if it doesn't exist in the sheet."""
    try:
        meta      = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        tab_names = [s["properties"]["title"] for s in meta["sheets"]]
        if "Linked Sources" in tab_names:
            return

        print("  [SM] Creating missing 'Linked Sources' tab...")
        service.spreadsheets().batchUpdate(
            spreadsheetId=sheet_id,
            body={"requests": [{"addSheet": {"properties": {
                "title":    "Linked Sources",
                "tabColor": {"red": 0.96, "green": 0.62, "blue": 0.04},
            }}}]}
        ).execute()

        # Write header row
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range="'Linked Sources'!A1",
            valueInputOption="RAW",
            body={"values": [[
                "Source Name", "URL",
                "Type (mine/competitor/inspiration)",
                "Added On", "Status", "Last Checked", "Notes",
            ]]},
        ).execute()
        print("  [SM] 'Linked Sources' tab created.")
    except Exception as e:
        print(f"  [SM] Could not create 'Linked Sources' tab: {e}")


# ── Main pipeline ─────────────────────────────────────────────────────────────

def run_source_manager():
    print("\n" + "=" * 62)
    print("  SOURCE MANAGER — syncing channel intelligence...")
    print("=" * 62)

    # ── Step 1: Get sheet ──────────────────────────────────────────────────
    sheet_id = get_sheet_id()
    if not sheet_id:
        print("  [SM] SOURCES_SHEET_ID not set — skipping source sync.")
        print("       Run: python setup_sources_sheet.py  (once, from your laptop)")
        return

    service = get_sheets_service()
    if not service:
        return

    ensure_schema()
    print(f"  Sheet ID: {sheet_id}")

    # ── Step 2: Read all source tabs ───────────────────────────────────────
    ensure_linked_sources_tab(service, sheet_id)

    yt_rows = read_tab(service, sheet_id, "YouTube Sources")
    ig_rows = read_tab(service, sheet_id, "Instagram Sources")
    li_rows = read_tab(service, sheet_id, "LinkedIn Sources")
    lk_rows = read_tab(service, sheet_id, "Linked Sources")

    yt_sources = parse_source_rows(yt_rows, "youtube")
    ig_sources = parse_source_rows(ig_rows, "instagram")
    li_sources = parse_source_rows(li_rows, "linkedin")
    lk_sources = parse_source_rows(lk_rows, "web")

    print(f"\n  Sources found:")
    print(f"    YouTube   : {len(yt_sources)}")
    print(f"    Instagram : {len(ig_sources)}")
    print(f"    LinkedIn  : {len(li_sources)}")
    print(f"    Linked    : {len(lk_sources)}")

    # ── Step 3: Sync all sources to DB ────────────────────────────────────
    for src in yt_sources + ig_sources + li_sources + lk_sources:
        upsert_source(src["platform"], src["name"], src["url"], src["source_type"])

    # ── Step 4: Analyse YouTube sources ───────────────────────────────────
    yt_key = os.environ.get("YOUTUBE_API_KEY", "").strip()
    if not yt_key:
        print("\n  [SM] YOUTUBE_API_KEY not set — skipping YouTube channel analysis.")
    else:
        print("\n  [4/5] Analysing YouTube channels...")
        channel_summaries = []

        for src in yt_sources:
            print(f"\n  [{src['source_type'].upper()}] {src['name']}")
            source_id = upsert_source("youtube", src["name"], src["url"], src["source_type"])
            analysis  = analyse_youtube_channel(
                src["name"], src["url"], src["source_type"], source_id
            )
            if not analysis:
                continue

            save_channel_analysis(source_id, analysis, analysis.get("videos_count", 0))

            # Update "Last Analyzed" cell in the sheet (col F = col 6)
            update_cell(
                service, sheet_id, "YouTube Sources",
                src["row_index"],        # already the exact Sheets row number
                6,
                datetime.now().strftime("%Y-%m-%d %H:%M")
            )

            channel_summaries.append(
                f"{src['name']} ({src['source_type']}): "
                f"{analysis.get('strategic_directions', '')}"
            )
            print(f"    ✓ Analysis saved.")

    # ── Step 5: Process new text inputs ───────────────────────────────────
    print("\n  [5/5] Processing New Inputs tab...")
    input_count = process_new_inputs(service, sheet_id)
    if input_count:
        print(f"    {input_count} new input(s) written to inputs/manual/insights.txt")
    else:
        print("    No new inputs this cycle.")

    # ── Step 6: Note for Instagram / LinkedIn / Linked ────────────────────
    manual_notes = []
    if ig_sources:
        names = ", ".join(s["name"] for s in ig_sources)
        manual_notes.append(f"Instagram ({names}): upload screenshots to Drive → "
                             f"inputs/whatsapp/personal_inbox/ for analysis.")
    if li_sources:
        names = ", ".join(s["name"] for s in li_sources)
        manual_notes.append(f"LinkedIn ({names}): upload screenshots to Drive for analysis.")
    if lk_sources:
        names = ", ".join(s["name"] for s in lk_sources)
        manual_notes.append(f"Linked sources ({names}): synced to DB — no automated fetch.")
    if manual_notes:
        print(f"\n  Manual input reminder: {'; '.join(manual_notes)}")

    # ── Step 7: Write Pipeline Status row ─────────────────────────────────
    all_sources = yt_sources + ig_sources + li_sources + lk_sources
    status_summary = (
        "; ".join(channel_summaries[:3]) if channel_summaries
        else f"Synced {len(all_sources)} sources. "
             f"YouTube API analysis {'complete' if yt_key else 'skipped (no API key)'}."
    )

    write_tab_row(
        service, sheet_id, "Pipeline Status",
        [
            datetime.now().strftime("%Y-%m-%d %H:%M UTC"),
            str(len(yt_sources)),
            str(len(ig_sources)),
            str(input_count),
            status_summary[:500],
            "Complete",
        ]
    )
    print("\n  Pipeline Status row written to sheet.")
    print("\n  [SM] Source manager complete.\n")


if __name__ == "__main__":
    run_source_manager()
