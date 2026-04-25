"""
WhatsApp Expert Signal Importer
---------------------------------
Formalises the 70-member expert WhatsApp community as a data source.

WORKFLOW:
  1. Generate this week's discussion prompt (or copy from output/drafts/):
       python whatsapp_import.py generate

  2. Send the prompt to the WhatsApp group manually.

  3. Collect expert responses into a plain text file — one response per
     line, or separated by blank lines or "---" dividers. Example:

       responses_2026-04-28.txt
       ──────────────────────────────
       Dr A: Seeing more late-diagnosed ADHD in post-menopausal women this month.
       ---
       Prof B: New preprint links estrogen withdrawal to dopamine transporter
       downregulation — could explain ADHD severity spikes in perimenopause.

  4. Import the file:
       python whatsapp_import.py import responses_2026-04-28.txt

  5. Run the intelligence pipeline as normal — expert signals are
     automatically included at highest priority in the next run:
       python run_all.py

Usage:
  python whatsapp_import.py generate              # show this week's prompt
  python whatsapp_import.py import <file.txt>     # import responses
  python whatsapp_import.py status                # show pending signals
"""

import sys
import os
import re
import argparse
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv(override=True)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))
import database as db


# ─────────────────────────────────────────────────────────────
# GENERATE — pull latest WhatsApp prompt from content_drafts
# ─────────────────────────────────────────────────────────────

def cmd_generate():
    db.init_schema()
    conn = db.get_connection()

    # Try to pull the most recent whatsapp draft from content_generator output
    row = conn.execute(
        """SELECT cd.post_text, cd.created_at, r.timestamp as run_ts
           FROM content_drafts cd
           JOIN runs r ON cd.run_id = r.id
           WHERE cd.platform = 'whatsapp'
           ORDER BY cd.id DESC LIMIT 1"""
    ).fetchone()

    latest_run = conn.execute(
        "SELECT id, timestamp FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()

    conn.close()

    print("\n" + "=" * 60)
    print("  WEEKLY WHATSAPP EXPERT DISCUSSION PROMPT")
    print("=" * 60)

    if row:
        print(f"  (from run: {row['run_ts'][:10]}  |  generated: {row['created_at'][:10]})")
        print()
        print(row["post_text"])
    else:
        print()
        print("  No prompt found in content_drafts.")
        print("  Run content_generator.py first to generate one:")
        print("    python content_generator.py")
        if latest_run:
            print(f"\n  Latest run available: id={latest_run['id']} ({latest_run['timestamp'][:10]})")

    print()
    print("=" * 60)
    print()
    print("  NEXT STEPS:")
    print("  1. Copy the prompt above and send to your WhatsApp expert group.")
    print("  2. Collect responses into a .txt file (one per line or separated by ---).")
    print("  3. Run: python whatsapp_import.py import <your_file.txt>")
    print()


# ─────────────────────────────────────────────────────────────
# PARSE — split a response file into individual signals
# ─────────────────────────────────────────────────────────────

def parse_responses(text: str) -> list[str]:
    """
    Accepts several formats:
      - One response per line (blank lines ignored)
      - Responses separated by --- or === dividers
      - "Name: response" format (Name label is stripped, response kept)
    Returns a list of clean, non-empty response strings.
    """
    # Split on dividers first
    blocks = re.split(r"\n\s*[-=]{3,}\s*\n", text)

    responses = []
    for block in blocks:
        block = block.strip()
        if not block:
            continue

        # If multi-line block, treat as one response
        lines = [l.strip() for l in block.splitlines() if l.strip()]
        if not lines:
            continue

        # Strip leading "Name:" label if present
        joined = " ".join(lines)
        cleaned = re.sub(r"^[A-Za-z][A-Za-z\s\.]+:\s*", "", joined).strip()

        if len(cleaned) > 20:
            responses.append(cleaned)

    # If no dividers found and only single-line blocks, try line-by-line
    if len(responses) <= 1 and "\n" in text:
        responses = []
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            cleaned = re.sub(r"^[A-Za-z][A-Za-z\s\.]+:\s*", "", line).strip()
            if len(cleaned) > 20:
                responses.append(cleaned)

    return responses


# ─────────────────────────────────────────────────────────────
# IMPORT — save parsed responses to whatsapp_signals table
# ─────────────────────────────────────────────────────────────

def cmd_import(filepath: str):
    db.init_schema()

    path = Path(filepath)
    if not path.exists():
        # Try relative to CWD, then BASE_DIR
        path = BASE_DIR / filepath
    if not path.exists():
        print(f"  [!] File not found: {filepath}")
        sys.exit(1)

    text = path.read_text(encoding="utf-8", errors="replace")
    responses = parse_responses(text)

    if not responses:
        print(f"  [!] No valid responses found in {path.name}.")
        print("  Make sure each response is on its own line or separated by ---")
        sys.exit(1)

    now = str(datetime.now())
    conn = db.get_connection()
    inserted = 0
    with conn:
        for r in responses:
            conn.execute(
                "INSERT INTO whatsapp_signals (imported_at, source_file, response) VALUES (?, ?, ?)",
                (now, path.name, r)
            )
            inserted += 1
    conn.close()

    print(f"\n  Imported {inserted} expert signal(s) from {path.name}")
    print("  These will be included at highest priority in the next run_all.py run.")
    print()
    print("  Preview:")
    for i, r in enumerate(responses, 1):
        print(f"    {i}. {r[:100]}{'...' if len(r) > 100 else ''}")
    print()


# ─────────────────────────────────────────────────────────────
# STATUS — show pending and recently used signals
# ─────────────────────────────────────────────────────────────

def cmd_status():
    db.init_schema()
    conn = db.get_connection()

    pending = conn.execute(
        "SELECT id, imported_at, source_file, response FROM whatsapp_signals WHERE used_run_id IS NULL ORDER BY imported_at"
    ).fetchall()

    used = conn.execute(
        """SELECT ws.id, ws.imported_at, ws.source_file, ws.response, ws.used_run_id, r.timestamp as run_ts
           FROM whatsapp_signals ws
           JOIN runs r ON ws.used_run_id = r.id
           ORDER BY ws.id DESC LIMIT 10"""
    ).fetchall()

    conn.close()

    print("\n" + "=" * 60)
    print("  WHATSAPP SIGNAL STATUS")
    print("=" * 60)

    print(f"\n  PENDING (will be used in next run): {len(pending)}")
    if pending:
        for s in pending:
            print(f"    [{s['id']}] {s['imported_at'][:10]} | {s['source_file'] or 'unknown'}")
            print(f"         {s['response'][:90]}{'...' if len(s['response']) > 90 else ''}")
    else:
        print("    None — import responses with: python whatsapp_import.py import <file.txt>")

    print(f"\n  RECENTLY USED (last 10):")
    if used:
        for s in used:
            print(f"    [{s['id']}] used in run #{s['used_run_id']} ({s['run_ts'][:10]})")
            print(f"         {s['response'][:90]}{'...' if len(s['response']) > 90 else ''}")
    else:
        print("    None yet.")

    print()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="WhatsApp expert signal importer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("generate", help="Show this week's discussion prompt")
    p_import = sub.add_parser("import", help="Import a responses text file")
    p_import.add_argument("file", help="Path to the .txt file containing expert responses")
    sub.add_parser("status", help="Show pending and recently used signals")

    args = parser.parse_args()

    if args.command == "generate":
        cmd_generate()
    elif args.command == "import":
        cmd_import(args.file)
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
