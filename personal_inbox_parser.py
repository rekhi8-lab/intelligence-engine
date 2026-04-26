"""
Personal Inbox Parser
---------------------
Processes WhatsApp chat exports from your personal saved-content group.

You save articles, URLs, screenshots, and text insights to a private
WhatsApp group. Export that chat and upload it to Google Drive:

  Drive → inputs → whatsapp → personal_inbox
    ├── chat.txt          (required — the exported chat)
    └── *.jpg / *.png     (optional — any media files from the export)

This script:
  1. Parses chat.txt — extracts messages, URLs, media references
  2. Classifies each item (url / text / image)
  3. Sends each to Claude for structured extraction
  4. Deduplicates by content hash (never processes the same item twice)
  5. Stores insights in founder_signals table
  6. Writes output/founder_signals/latest_signals.txt to Drive

Runs inside GitHub Actions after nervous_system.py.
"""

import os
import sys
import re
import json
import base64
import hashlib
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
import anthropic

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR       = Path(__file__).parent
INBOX_DIR      = BASE_DIR / "inputs" / "whatsapp" / "personal_inbox"
MANUAL_FILE    = BASE_DIR / "inputs" / "manual" / "insights.txt"
SIGNALS_DIR    = BASE_DIR / "output" / "founder_signals"
SIGNALS_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(BASE_DIR))
import database as db

client = anthropic.Anthropic(api_key=os.getenv("CLAUDE_API_KEY"))

# WhatsApp export line patterns (Android and iOS)
WA_ANDROID = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}(?:\s?[AP]M)?)\s-\s([^:]+):\s(.*)$"
)
WA_IOS = re.compile(
    r"^\[(\d{1,2}/\d{1,2}/\d{2,4}),\s(\d{1,2}:\d{2}:\d{2}\s?[AP]M)\]\s([^:]+):\s(.*)$"
)
URL_RE     = re.compile(r"https?://[^\s\]\)>\"\']+")
MEDIA_RE   = re.compile(r"(IMG|VID|AUD|DOC|PTT)-\d{8}-WA\d+\.\w+|<Media omitted>|\.(jpg|jpeg|png|pdf)(\s|$)", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────
# STEP 1 — PARSE WHATSAPP CHAT
# ─────────────────────────────────────────────────────────────

def parse_whatsapp_chat(chat_path: Path) -> list[dict]:
    """Parse a WhatsApp .txt export into a list of message dicts."""
    messages = []
    current  = None

    with open(chat_path, "r", encoding="utf-8", errors="replace") as f:
        for raw_line in f:
            line = raw_line.rstrip()
            m    = WA_ANDROID.match(line) or WA_IOS.match(line)
            if m:
                if current:
                    messages.append(current)
                current = {
                    "date":    m.group(1),
                    "time":    m.group(2),
                    "sender":  m.group(3).strip(),
                    "content": m.group(4).strip(),
                }
            elif current and line:
                # continuation of previous message
                current["content"] += " " + line.strip()

    if current:
        messages.append(current)

    # Filter out system messages (e.g. "Messages and calls are end-to-end encrypted")
    return [
        m for m in messages
        if m["content"] and not m["content"].startswith("Messages and calls")
        and "end-to-end encrypted" not in m["content"]
        and m["content"] != "<Media omitted>"
    ]


# ─────────────────────────────────────────────────────────────
# STEP 2 — CLASSIFY CONTENT
# ─────────────────────────────────────────────────────────────

def classify_message(msg: dict, inbox_dir: Path) -> list[dict]:
    """
    A single WhatsApp message can produce multiple items:
      - One per URL found in the text
      - One image item if the message references a media file that exists locally
      - One text item if it has meaningful non-URL text
    Returns a list of {type, content, context} dicts.
    """
    items   = []
    content = msg["content"]

    # Extract URLs
    urls = URL_RE.findall(content)
    for url in urls:
        items.append({"type": "url", "content": url, "context": content})

    # Check for media file references
    media_m = re.search(r"([\w\-]+\.(jpg|jpeg|png|pdf))", content, re.IGNORECASE)
    if media_m:
        fname = media_m.group(1)
        fpath = inbox_dir / fname
        if fpath.exists():
            ext = fpath.suffix.lower()
            if ext in (".jpg", ".jpeg", ".png"):
                items.append({"type": "image", "content": str(fpath), "context": content})
            elif ext == ".pdf":
                items.append({"type": "pdf",   "content": str(fpath), "context": content})

    # Extract meaningful text (strip URLs out, check if anything substantive remains)
    text_only = URL_RE.sub("", content).strip()
    text_only = re.sub(r"[\w\-]+\.(jpg|jpeg|png|pdf)", "", text_only, flags=re.IGNORECASE).strip()
    if len(text_only) > 40 and not MEDIA_RE.search(text_only):
        items.append({"type": "text", "content": text_only, "context": content})

    return items


# ─────────────────────────────────────────────────────────────
# STEP 3 — DEDUPLICATION
# ─────────────────────────────────────────────────────────────

def hash_content(text: str) -> str:
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def is_duplicate(content_hash: str) -> bool:
    conn = db.get_connection()
    row  = conn.execute(
        "SELECT id FROM founder_signals WHERE content_hash = ?", (content_hash,)
    ).fetchone()
    conn.close()
    return row is not None


# ─────────────────────────────────────────────────────────────
# STEP 4 — CLAUDE EXTRACTION (one call per item)
# ─────────────────────────────────────────────────────────────

def fetch_url_preview(url: str) -> str:
    """Try to fetch page title/description as context for Claude. Graceful fallback."""
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            html = resp.read(8000).decode("utf-8", errors="replace")
        title = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        desc  = re.search(r'<meta[^>]+name=["\']description["\'][^>]+content=["\'](.*?)["\']', html, re.IGNORECASE)
        parts = []
        if title:
            parts.append(f"Title: {title.group(1).strip()[:200]}")
        if desc:
            parts.append(f"Description: {desc.group(1).strip()[:300]}")
        return "\n".join(parts) if parts else ""
    except Exception:
        return ""


def analyze_url(url: str, context: str) -> dict:
    preview = fetch_url_preview(url)
    prompt  = f"""You are an intelligence analyst for a Women's Health content strategy system.

Analyse this URL and extract a structured signal.

URL: {url}
Page preview: {preview if preview else '(could not fetch — use the URL and context below)'}
Context from the person who shared it: {context[:300]}

Return ONLY valid JSON:
{{
  "topic": "specific topic this is about (1 sentence)",
  "insight": "the key insight or finding (2-3 sentences)",
  "emotional_signal": "what emotional need or pain point this connects to",
  "content_angle": "one specific content idea this suggests for a women's health brand"
}}"""
    return _call_claude_text(prompt)


def analyze_text(text: str) -> dict:
    prompt = f"""You are an intelligence analyst for a Women's Health content strategy system.

Extract a structured signal from this text insight shared by a founder:

TEXT: {text[:800]}

Return ONLY valid JSON:
{{
  "topic": "specific topic this is about (1 sentence)",
  "insight": "the key insight (2-3 sentences)",
  "emotional_signal": "what emotional need or pain point this connects to",
  "content_angle": "one specific content idea this suggests for a women's health brand"
}}"""
    return _call_claude_text(prompt)


def analyze_image(image_path: str, context: str) -> dict:
    path = Path(image_path)
    if not path.exists():
        return {}
    ext      = path.suffix.lower()
    mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
    mime     = mime_map.get(ext, "image/jpeg")
    img_data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")

    prompt_text = (
        f"You are an intelligence analyst for a Women's Health content strategy system.\n\n"
        f"Analyse this screenshot/image and extract a structured signal.\n"
        f"Context from the person who saved it: {context[:300]}\n\n"
        "Return ONLY valid JSON:\n"
        "{\n"
        '  "topic": "specific topic shown in this image (1 sentence)",\n'
        '  "insight": "the key insight or finding (2-3 sentences)",\n'
        '  "emotional_signal": "what emotional need or pain point this connects to",\n'
        '  "content_angle": "one specific content idea this suggests for a women\'s health brand"\n'
        "}"
    )

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": mime,
                            "data":       img_data,
                        },
                    },
                    {"type": "text", "text": prompt_text},
                ],
            }],
        )
        return _parse_json(response.content[0].text)
    except Exception as e:
        print(f"  [PI] Image analysis error: {e}")
        return {}


def _call_claude_text(prompt: str) -> dict:
    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=600,
            temperature=0.3,
            messages=[{"role": "user", "content": prompt}]
        )
        return _parse_json(response.content[0].text)
    except Exception as e:
        print(f"  [PI] Claude error: {e}")
        return {}


def _parse_json(text: str) -> dict:
    import re as _re
    text = _re.sub(r"```(?:json)?\s*", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = _re.search(r"\{.*\}", text, _re.DOTALL)
        if m:
            try:
                return json.loads(m.group())
            except Exception:
                pass
    return {}


# ─────────────────────────────────────────────────────────────
# STEP 5 — STORE IN DB
# ─────────────────────────────────────────────────────────────

def store_signal(source_type: str, extracted: dict, raw_content: str) -> bool:
    if not extracted or not extracted.get("insight"):
        return False
    content_hash = hash_content(raw_content)
    if is_duplicate(content_hash):
        return False
    conn = db.get_connection()
    with conn:
        conn.execute(
            """INSERT INTO founder_signals
               (source_type, topic, insight, emotional_signal, content_angle,
                raw_content, content_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                source_type,
                extracted.get("topic", "")[:500],
                extracted.get("insight", "")[:1000],
                extracted.get("emotional_signal", "")[:500],
                extracted.get("content_angle", "")[:500],
                raw_content[:1000],
                content_hash,
            )
        )
    conn.close()
    return True


# ─────────────────────────────────────────────────────────────
# STEP 6 — WRITE OUTPUT FILE
# ─────────────────────────────────────────────────────────────

def write_signals_output(new_signals: list[dict]):
    ts     = datetime.now().strftime("%Y-%m-%d %H:%M UTC")
    lines  = [
        "=" * 62,
        "  FOUNDER SIGNALS — Latest Processed",
        f"  Generated : {ts}",
        f"  New this run: {len(new_signals)}",
        "=" * 62,
    ]

    if not new_signals:
        lines += ["", "  No new signals processed this run.", ""]
    else:
        for i, s in enumerate(new_signals, 1):
            lines += [
                "",
                f"  [{i}] {s.get('source_type', '').upper()} — {s.get('topic', 'N/A')}",
                "  " + "─" * 58,
                f"  Insight        : {s.get('insight', 'N/A')}",
                f"  Emotional signal: {s.get('emotional_signal', 'N/A')}",
                f"  Content angle  : {s.get('content_angle', 'N/A')}",
            ]

    lines.append("\n" + "=" * 62)
    out_path = SIGNALS_DIR / "latest_signals.txt"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"  [PI] Signals written → {out_path}")


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def parse_manual_inputs(file_path: Path) -> list[dict]:
    """
    Read plain-text inputs submitted via the nudge UI.
    Each non-empty line is treated as a URL or text insight.
    Returns a list of {type, content, context} items.
    """
    if not file_path.exists():
        return []
    items   = []
    content = file_path.read_text(encoding="utf-8", errors="replace")
    # Split on session separators written by nudge_ui.py
    blocks  = re.split(r"---\s*Submitted.*?---", content, flags=re.DOTALL)
    for block in blocks:
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            urls = URL_RE.findall(line)
            for url in urls:
                items.append({"type": "url", "content": url, "context": line})
            text = URL_RE.sub("", line).strip()
            if len(text) > 40:
                items.append({"type": "text", "content": text, "context": line})
    return items


def run_inbox_parser():
    print("\n" + "=" * 62)
    print("  PERSONAL INBOX PARSER — processing founder signals...")
    print("=" * 62)

    db.init_schema()

    # ── Manual inputs from nudge UI ───────────────────────────
    manual_items = parse_manual_inputs(MANUAL_FILE)
    if manual_items:
        print(f"  [PI] {len(manual_items)} manual input(s) from nudge UI")
    else:
        print("  [PI] No manual inputs from nudge UI this cycle")

    chat_path = INBOX_DIR / "chat.txt"
    if not chat_path.exists() and not manual_items:
        print(f"  [PI] No chat.txt found and no manual inputs.")
        print("  [PI] Run: python nudge_ui.py  to add inputs.")
        write_signals_output([])
        return

    # ── WhatsApp chat export ──────────────────────────────────
    all_items = list(manual_items)   # start with manual inputs

    if chat_path.exists():
        print(f"  [PI] Parsing {chat_path.name}...")
        messages = parse_whatsapp_chat(chat_path)
        print(f"  [PI] {len(messages)} messages found in chat export")
        for msg in messages:
            items = classify_message(msg, INBOX_DIR)
            all_items.extend(items)
    else:
        print("  [PI] No WhatsApp chat export found — processing manual inputs only")

    # Deduplicate before calling Claude
    novel_items = []
    for item in all_items:
        h = hash_content(item["content"])
        if not is_duplicate(h):
            novel_items.append(item)

    print(f"  [PI] {len(all_items)} total items → {len(novel_items)} new (not yet processed)")

    if not novel_items:
        print("  [PI] Nothing new to process.")
        write_signals_output([])
        return

    # Process each novel item
    new_signals = []
    url_count = text_count = img_count = 0

    for item in novel_items:
        itype = item["type"]
        try:
            if itype == "url":
                url_count += 1
                print(f"  [PI] URL {url_count}: {item['content'][:60]}...")
                extracted = analyze_url(item["content"], item["context"])
            elif itype == "text":
                text_count += 1
                print(f"  [PI] Text {text_count}: {item['content'][:60]}...")
                extracted = analyze_text(item["content"])
            elif itype == "image":
                img_count += 1
                fname = Path(item["content"]).name
                print(f"  [PI] Image {img_count}: {fname}")
                extracted = analyze_image(item["content"], item["context"])
            else:
                continue

            stored = store_signal(itype, extracted, item["content"])
            if stored:
                new_signals.append({**extracted, "source_type": itype})
                print(f"       → Stored: {extracted.get('topic', 'N/A')[:60]}")
            else:
                print(f"       → Skipped (duplicate or empty)")

        except Exception as e:
            print(f"  [PI] Error processing {itype} item: {e}")
            continue

    print(f"\n  [PI] Summary: {len(new_signals)} new signals stored")
    print(f"       URLs: {url_count} | Text: {text_count} | Images: {img_count}")

    write_signals_output(new_signals)

    # Archive manual inputs so they're not reprocessed next run
    if MANUAL_FILE.exists() and manual_items:
        archive = MANUAL_FILE.parent / "insights_processed.txt"
        existing = archive.read_text(encoding="utf-8") if archive.exists() else ""
        archive.write_text(
            existing + MANUAL_FILE.read_text(encoding="utf-8"), encoding="utf-8"
        )
        MANUAL_FILE.write_text("", encoding="utf-8")   # clear for next cycle
        print(f"  [PI] Manual inputs archived and cleared for next cycle")

    print("\n  [PI] Inbox parser complete.\n")


if __name__ == "__main__":
    run_inbox_parser()
