"""
Run Input Collector
-------------------
Collects per-run inputs at the start of every pipeline run — scheduled or manual.

For ALL runs (scheduled + manual):
  - Sends a Telegram message to the configured chat
  - Polls for up to INPUT_TIMEOUT_SECS (default 300 = 5 min) using long-polling
  - Accepts commands:
      /youtube  Name | URL | mine/competitor/inspiration
      /instagram Name | @handle_or_URL | mine/competitor/inspiration
      /link     Name | URL | mine/competitor/inspiration
      /insight  Your text here
      📎 File drop — .txt (insight notes or WhatsApp export) or .zip (WhatsApp export)
      /done  — proceed immediately
      /skip  — skip all inputs and proceed
  - Writes new sources to tracked_sources DB
  - Writes text insights to inputs/manual/insights.txt
  - Writes uploaded files to inputs/manual/ or inputs/whatsapp/personal_inbox/

For manual (workflow_dispatch) runs:
  - Also reads DISPATCH_* env vars from the GitHub form inputs
  - Processes dispatch inputs first, then opens the Telegram window

GitHub secrets required:
  TELEGRAM_BOT_TOKEN  — from @BotFather on Telegram
  TELEGRAM_CHAT_ID    — your personal chat ID (send /start to @userinfobot to find it)

Optional env vars:
  INPUT_TIMEOUT_SECS  — window length in seconds (default 300)

Graceful degradation:
  - If TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set, Telegram step is skipped
  - If DISPATCH_SKIP=true, entire module exits immediately
"""

import os
import sys
import json
import time
import zipfile
import tempfile
import re
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR      = Path(__file__).parent
MANUAL_DIR    = BASE_DIR / "inputs" / "manual"
WHATSAPP_DIR  = BASE_DIR / "inputs" / "whatsapp" / "personal_inbox"
INSIGHTS_FILE = MANUAL_DIR / "insights.txt"
MANUAL_DIR.mkdir(parents=True, exist_ok=True)
WHATSAPP_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
import database as db

BOT_TOKEN    = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID      = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
TIMEOUT_SECS = int(os.environ.get("INPUT_TIMEOUT_SECS", "300"))
TG_API       = f"https://api.telegram.org/bot{BOT_TOKEN}"


# ─────────────────────────────────────────────────────────────
# TELEGRAM HELPERS
# ─────────────────────────────────────────────────────────────

def tg_send(text: str):
    """Send a message to the configured Telegram chat."""
    if not BOT_TOKEN or not CHAT_ID:
        return
    import requests
    try:
        requests.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"  [TG] sendMessage failed: {e}")


def tg_get_updates(offset: int = 0, long_poll: int = 20) -> list:
    """Fetch updates with long-polling (waits up to long_poll seconds server-side)."""
    if not BOT_TOKEN:
        return []
    import requests
    try:
        resp = requests.get(
            f"{TG_API}/getUpdates",
            params={"offset": offset, "timeout": long_poll},
            timeout=long_poll + 5,
        )
        return resp.json().get("result", [])
    except Exception:
        return []


def tg_get_start_offset() -> int:
    """Return update_id + 1 for the latest existing update (ignores pre-run messages)."""
    updates = tg_get_updates(offset=-1, long_poll=0)
    if updates:
        return updates[-1]["update_id"] + 1
    return 0


def tg_download_file(file_id: str, dest: Path):
    """Download a Telegram document to dest."""
    import requests
    resp = requests.get(f"{TG_API}/getFile", params={"file_id": file_id}, timeout=10)
    resp.raise_for_status()
    file_path = resp.json()["result"]["file_path"]
    file_url  = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    dest.write_bytes(requests.get(file_url, timeout=60).content)


# ─────────────────────────────────────────────────────────────
# PARSING HELPERS
# ─────────────────────────────────────────────────────────────

_WHATSAPP_PATTERN = re.compile(
    r"(\d{1,2}/\d{1,2}/\d{2,4})"    # Android date
    r"|(\[\d{1,2}/\d{1,2}/\d{2,4})", # iOS date
)

def normalise_type(raw: str) -> str:
    raw = raw.strip().lower()
    if raw in ("mine", "my", "ours", "our"):
        return "mine"
    if raw in ("inspiration", "inspo", "inspire", "inspired"):
        return "inspiration"
    return "competitor"


def parse_pipe(text: str) -> tuple[str, str, str]:
    """
    Parse 'Name | URL | type' or 'Name | URL' or just 'URL'.
    Returns (name, url, type).
    """
    parts = [p.strip() for p in text.split("|")]
    name  = parts[0] if len(parts) > 0 else ""
    url   = parts[1] if len(parts) > 1 else ""
    stype = normalise_type(parts[2]) if len(parts) > 2 else "competitor"

    # If name looks like a URL and url is empty, swap
    if not url and (name.startswith("http") or name.startswith("@")):
        url, name = name, ""

    return name, url, stype


def is_whatsapp_export(text: str) -> bool:
    return bool(_WHATSAPP_PATTERN.search(text[:600]))


# ─────────────────────────────────────────────────────────────
# DB MIGRATION
# ─────────────────────────────────────────────────────────────

def ensure_tracked_sources_schema():
    """Add any missing columns to tracked_sources (old DBs may be missing them)."""
    conn = db.get_connection()
    existing_cols = {
        row[1]
        for row in conn.execute("PRAGMA table_info(tracked_sources)").fetchall()
    }
    migrations = {
        "source_url":    "ALTER TABLE tracked_sources ADD COLUMN source_url    TEXT",
        "source_type":   "ALTER TABLE tracked_sources ADD COLUMN source_type   TEXT",
        "channel_id":    "ALTER TABLE tracked_sources ADD COLUMN channel_id    TEXT",
        "last_analyzed": "ALTER TABLE tracked_sources ADD COLUMN last_analyzed DATETIME",
        "last_used":     "ALTER TABLE tracked_sources ADD COLUMN last_used     DATETIME",
        "notes":         "ALTER TABLE tracked_sources ADD COLUMN notes         TEXT",
    }
    with conn:
        for col, sql in migrations.items():
            if col not in existing_cols:
                try:
                    conn.execute(sql)
                except Exception:
                    pass
    conn.close()


# ─────────────────────────────────────────────────────────────
# DB WRITE HELPERS
# ─────────────────────────────────────────────────────────────

def add_source(platform: str, name: str, url: str, stype: str) -> bool:
    """
    Upsert a source into tracked_sources.
    Returns True if a new row was inserted, False if updated.
    """
    conn = db.get_connection()
    existing = conn.execute(
        "SELECT id FROM tracked_sources WHERE platform=? AND source_name=?",
        (platform, name or url),
    ).fetchone()
    with conn:
        if existing:
            conn.execute(
                "UPDATE tracked_sources SET source_url=?, source_type=? WHERE id=?",
                (url, stype, existing["id"]),
            )
            conn.close()
            return False
        conn.execute(
            """INSERT INTO tracked_sources (platform, source_name, source_url, source_type)
               VALUES (?, ?, ?, ?)""",
            (platform, name or url, url, stype),
        )
    conn.close()
    return True


def add_insight(text: str, label: str = "Telegram"):
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    with open(INSIGHTS_FILE, "a", encoding="utf-8") as f:
        f.write(f"\n\n--- Added via {label} {stamp} ---\n")
        f.write(text.strip() + "\n")


# ─────────────────────────────────────────────────────────────
# FILE HANDLING
# ─────────────────────────────────────────────────────────────

def handle_file(file_id: str, file_name: str) -> str:
    """
    Download a Telegram file and route it:
      .zip → extract .txt files to inputs/whatsapp/personal_inbox/
      .txt → WhatsApp export → whatsapp/personal_inbox/, else → insights.txt
    Returns a human-readable result string.
    """
    suffix   = Path(file_name).suffix.lower()
    tmp_dir  = Path(tempfile.mkdtemp())
    tmp_file = tmp_dir / file_name

    try:
        tg_download_file(file_id, tmp_file)
    except Exception as e:
        return f"Download failed: {e}"

    if suffix == ".zip":
        try:
            with zipfile.ZipFile(tmp_file) as zf:
                txt_members = [m for m in zf.namelist() if m.lower().endswith(".txt")]
                for member in txt_members:
                    dest = WHATSAPP_DIR / Path(member).name
                    dest.write_bytes(zf.read(member))
            if txt_members:
                return (f"ZIP extracted — {len(txt_members)} .txt file(s) saved to "
                        f"inputs/whatsapp/personal_inbox/")
            return "ZIP opened but contained no .txt files."
        except zipfile.BadZipFile:
            return "File is not a valid ZIP."

    elif suffix == ".txt":
        content = tmp_file.read_text(encoding="utf-8", errors="replace")
        if is_whatsapp_export(content):
            dest = WHATSAPP_DIR / file_name
            dest.write_bytes(tmp_file.read_bytes())
            return f"WhatsApp export saved to inputs/whatsapp/personal_inbox/{file_name}"
        else:
            add_insight(content, label="file upload")
            return f"Insight file appended to inputs/manual/insights.txt"

    return f"Unsupported file type '{suffix}' — send .txt or .zip"


# ─────────────────────────────────────────────────────────────
# COMMAND DISPATCHER
# ─────────────────────────────────────────────────────────────

HELP_TEXT = (
    "Commands:\n"
    "/youtube  Name | URL | mine/competitor/inspiration\n"
    "/instagram Name | @handle | mine/competitor/inspiration\n"
    "/link  Name | URL | mine/competitor/inspiration\n"
    "/insight Your text note here\n"
    "📎 Send a .txt or .zip file (WhatsApp export or insight notes)\n\n"
    "/done — proceed immediately\n"
    "/skip — skip all remaining inputs\n"
    "/help — show this message"
)

def dispatch(text: str) -> str | None:
    """
    Route a Telegram message during the scheduled window.
    /done|/skip|/proceed end the window immediately.
    All other commands delegated to input_handlers.
    """
    lower = text.strip().lower()

    if lower in ("/done", "/skip", "/proceed", "done", "skip"):
        return "__DONE__"

    _AW_DIR = os.environ.get("AUTOMATION_WORKING_FOLDER", "/home/Acer/automation-working-folder")
    if _AW_DIR not in sys.path:
        sys.path.insert(0, _AW_DIR)
    try:
        from input_handlers import handle_command
        return handle_command(text.strip())
    except ImportError as exc:
        print(f"  [WARN] input_handlers ImportError ({exc}); path={_AW_DIR!r} — falling back to legacy dispatch")
        tg_send(
            f"⚠️ <b>[input_collector]</b> primary handler unreachable "
            f"(<code>{exc}</code>). "
            f"Some commands may not be fully processed. "
            f"Check AUTOMATION_WORKING_FOLDER on the VM."
        )
        return _legacy_dispatch(text.strip())


def _legacy_dispatch(text: str) -> str | None:
    """Fallback if input_handlers unavailable."""
    lower = text.strip().lower()
    if re.match(r"^/?youtube\b", lower):
        raw = re.sub(r"^/?youtube\s*", "", text, flags=re.IGNORECASE).strip()
        if raw:
            n, u, t = parse_pipe(raw)
            add_source("youtube", n, u, t)
            return f"✅ YouTube added: {n or u}"
    if re.match(r"^/?instagram\b", lower):
        raw = re.sub(r"^/?instagram\s*", "", text, flags=re.IGNORECASE).strip()
        if raw:
            n, u, t = parse_pipe(raw)
            add_source("instagram", n, u, t)
            return f"✅ Instagram added: {n or u}"
    if re.match(r"^/?link\b", lower):
        raw = re.sub(r"^/?link\s*", "", text, flags=re.IGNORECASE).strip()
        if raw:
            n, u, t = parse_pipe(raw)
            add_source("web", n, u, t)
            return f"✅ Link added: {n or u}"
    if re.match(r"^/?insight\b", lower):
        raw = re.sub(r"^/?insight\s*", "", text, flags=re.IGNORECASE).strip()
        if raw:
            add_insight(raw)
            return "✅ Insight saved"
    if lower in ("/help", "help", "?"):
        return HELP_TEXT
    return None

# ─────────────────────────────────────────────────────────────
# TELEGRAM POLL LOOP
# ─────────────────────────────────────────────────────────────

def poll_telegram(timeout_secs: int) -> dict:
    """
    Long-poll Telegram for up to timeout_secs seconds.
    Returns {"added": [str, ...], "done_early": bool}
    """
    added    = []
    offset   = tg_get_start_offset()
    deadline = time.time() + timeout_secs

    print(f"  [TG] Polling until "
          f"{datetime.utcfromtimestamp(deadline).strftime('%H:%M:%S')} UTC "
          f"({timeout_secs // 60}m {timeout_secs % 60}s)...")

    while time.time() < deadline:
        remaining    = int(deadline - time.time())
        long_poll    = min(20, remaining)
        if long_poll <= 0:
            break

        updates = tg_get_updates(offset=offset, long_poll=long_poll)

        for upd in updates:
            offset = upd["update_id"] + 1
            msg    = upd.get("message", {})

            # Only respond to our configured chat
            if str(msg.get("chat", {}).get("id", "")) != str(CHAT_ID):
                continue

            # ── File upload ───────────────────────────────────
            doc = msg.get("document")
            if doc:
                result = handle_file(doc["file_id"], doc.get("file_name", "upload.txt"))
                tg_send(f"✅ {result}")
                added.append(f"file:{doc.get('file_name', '?')}")
                continue

            # ── Text command ──────────────────────────────────
            text = msg.get("text", "").strip()
            if not text:
                continue

            reply = dispatch(text)
            if reply == "__DONE__":
                tg_send("✅ Got it — pipeline proceeding now.")
                return {"added": added, "done_early": True}
            if reply:
                tg_send(reply)
                if reply.startswith("✅"):
                    added.append(text[:70])

    return {"added": added, "done_early": False}


# ─────────────────────────────────────────────────────────────
# WORKFLOW DISPATCH INPUTS
# ─────────────────────────────────────────────────────────────

def process_dispatch_inputs() -> list[str]:
    """
    Read DISPATCH_* env vars populated from the workflow_dispatch form.
    Processes them directly without waiting.
    Returns list of items added.
    """
    added = []

    def _clean(val: str) -> str:
        return val.strip() if val.strip().lower() not in ("", "skip", "none", "-") else ""

    yt = _clean(os.environ.get("DISPATCH_YOUTUBE", ""))
    if yt:
        name, url, stype = parse_pipe(yt)
        if name or url:
            is_new = add_source("youtube", name, url, stype)
            print(f"  [DISPATCH] {'Added' if is_new else 'Updated'} YouTube: {name or url} [{stype}]")
            added.append(f"youtube:{name or url}")

    ig = _clean(os.environ.get("DISPATCH_INSTAGRAM", ""))
    if ig:
        name, url, stype = parse_pipe(ig)
        if name or url:
            is_new = add_source("instagram", name, url, stype)
            print(f"  [DISPATCH] {'Added' if is_new else 'Updated'} Instagram: {name or url} [{stype}]")
            added.append(f"instagram:{name or url}")

    lk = _clean(os.environ.get("DISPATCH_LINK", ""))
    if lk:
        name, url, stype = parse_pipe(lk)
        if name or url:
            is_new = add_source("web", name, url, stype)
            print(f"  [DISPATCH] {'Added' if is_new else 'Updated'} Link: {name or url} [{stype}]")
            added.append(f"link:{name or url}")

    insight = _clean(os.environ.get("DISPATCH_INSIGHT", ""))
    if insight:
        add_insight(insight, label="workflow_dispatch form")
        print(f"  [DISPATCH] Insight saved ({len(insight)} chars)")
        added.append("insight")

    return added


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def run_input_collector():
    print("\n" + "=" * 62)
    print("  INPUT COLLECTOR — opening input window...")
    print("=" * 62)

    db.init_schema()
    ensure_tracked_sources_schema()

    # ── Hard skip (DISPATCH_SKIP=true) ────────────────────────
    if os.environ.get("DISPATCH_SKIP", "").lower() in ("true", "1", "yes"):
        print("  DISPATCH_SKIP=true — skipping input collection.")
        return

    # ── Step 1: Process workflow_dispatch form ─────────────────
    dispatch_items = process_dispatch_inputs()
    if dispatch_items:
        print(f"  Dispatch form: {len(dispatch_items)} item(s) processed.")

    # ── Step 2: Telegram notification + polling ─────────────────
    if not BOT_TOKEN or not CHAT_ID:
        print("  [TG] TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not configured.")
        print("       Set both as GitHub Secrets to enable the Telegram input window.")
        if not dispatch_items:
            print("  No inputs collected this run.")
        return

    minutes   = TIMEOUT_SECS // 60
    secs_rem  = TIMEOUT_SECS % 60
    run_event = os.environ.get("GITHUB_EVENT_NAME", "scheduled")
    run_time  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    prompt = (
        f"🚀 <b>Intelligence Pipeline starting</b>\n"
        f"<i>{run_time} · {run_event} run</i>\n\n"
        f"⏱ You have <b>{minutes}m {secs_rem}s</b> to add inputs before the pipeline proceeds.\n\n"
        f"<b>Add sources:</b>\n"
        f"/youtube  Name | URL | mine / competitor / inspiration\n"
        f"/instagram  Name | @handle | mine / competitor / inspiration\n"
        f"/link  Name | URL | mine / competitor / inspiration\n\n"
        f"<b>Add content:</b>\n"
        f"/insight  Your note, observation, or article summary\n"
        f"📎 Send a <b>.txt</b> or <b>.zip</b> file (WhatsApp export or insight notes)\n\n"
        f"/done — proceed now   /skip — skip inputs   /help — show commands"
    )

    tg_send(prompt)
    print(f"  [TG] Prompt sent to chat {CHAT_ID}.")
    print(f"  [TG] Waiting up to {minutes}m {secs_rem}s for inputs...")

    result = poll_telegram(TIMEOUT_SECS)

    total = len(dispatch_items) + len(result["added"])

    if result["done_early"]:
        print("  [TG] User sent /done — proceeding early.")
    else:
        print(f"  [TG] Timeout reached.")

    if total:
        print(f"  ✓ {total} input(s) collected this run.")
        tg_send(
            f"✅ Input window closed.\n"
            f"{total} new input(s) added — pipeline continuing."
        )
    else:
        print("  No new inputs this run.")
        tg_send("✅ No inputs added — pipeline continuing.")

    print()


if __name__ == "__main__":
    run_input_collector()
