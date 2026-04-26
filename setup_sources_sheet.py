"""
Setup Sources Sheet — run ONCE from your laptop
------------------------------------------------
Creates the Google Sheet that acts as your cloud-native control panel.
The pipeline reads and writes to this sheet automatically every run.

Run:
  python setup_sources_sheet.py

What it does:
  1. Opens a browser for Google OAuth (your personal account)
  2. Creates a Google Sheet called "Intelligence Engine — Sources"
  3. Sets up 5 tabs with correct headers
  4. Pre-populates YouTube/Instagram rows from your existing tracked_sources DB
  5. Prints the Sheet ID → add as SOURCES_SHEET_ID to GitHub Secrets + .env

After running:
  1. Copy the Sheet ID printed at the end
  2. Add to GitHub Secrets:  SOURCES_SHEET_ID = <id>
  3. Add to your .env file:  SOURCES_SHEET_ID=<id>
  4. Share the sheet with your service account email (Editor access)
     (service account email is printed during setup)

Auth:
  Reuses credentials.json + token.json from the same OAuth flow as
  gdrive_backup.py. If you have already run that script, this will
  open the browser to confirm Sheets scope.
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent

# ── Google auth ──────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.file",
]

CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TOKEN_FILE       = BASE_DIR / "sheets_token.json"

def get_oauth_service():
    """Obtain OAuth2 credentials for Sheets API (personal account)."""
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDENTIALS_FILE.exists():
                print(f"\n[!] credentials.json not found at: {CREDENTIALS_FILE}")
                print("    Download it from Google Cloud Console → APIs & Services → Credentials.")
                print("    (OAuth 2.0 Client ID, Desktop app type)")
                sys.exit(1)
            flow  = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)
        TOKEN_FILE.write_text(creds.to_json())

    return build("sheets", "v4", credentials=creds)


# ── Sheet structure ──────────────────────────────────────────────────────────

SHEET_NAME = "Intelligence Engine — Sources"

TABS = {
    "YouTube Sources": [
        ["Channel Name", "Channel URL", "Type (mine/competitor/inspiration)",
         "Added On", "Status", "Last Analyzed", "Subscriber Count", "Notes"]
    ],
    "Instagram Sources": [
        ["Account Name", "Instagram URL / Handle", "Type (mine/competitor/inspiration)",
         "Added On", "Status", "Last Analyzed", "Follower Count", "Notes"]
    ],
    "LinkedIn Sources": [
        ["Account / Page Name", "LinkedIn URL", "Type (mine/competitor/inspiration)",
         "Added On", "Status", "Last Analyzed", "Followers", "Notes"]
    ],
    "Linked Sources": [
        ["Source Name", "URL", "Type (mine/competitor/inspiration)",
         "Added On", "Status", "Last Checked", "Notes"]
    ],
    "New Inputs": [
        ["Content (text, URL, or insight)", "Added On", "Processed"]
    ],
    "Pipeline Status": [
        ["Run Date", "YouTube Sources", "Instagram Sources",
         "New Inputs Processed", "Key Insights (this run)", "Status"]
    ],
}

STARTER_ROWS = {
    "YouTube Sources": [
        ["Global Menopause Collective", "https://www.youtube.com/@GlobalMenopauseCollective",
         "mine", datetime.now().strftime("%Y-%m-%d"), "Active", "", "", ""],
        ["Endo Neutral", "", "mine",
         datetime.now().strftime("%Y-%m-%d"), "Active", "", "", ""],
    ],
    "Instagram Sources": [
        ["Global Menopause Collective", "https://www.instagram.com/globalmenopausecollective",
         "mine", datetime.now().strftime("%Y-%m-%d"), "Active", "", "", ""],
    ],
    "LinkedIn Sources": [],
    "Linked Sources": [],
    "New Inputs": [],
    "Pipeline Status": [
        ["(pipeline will write here automatically)", "", "", "", "", ""]
    ],
}


def hex_to_rgb(hex_color: str) -> dict:
    """Convert '#RRGGBB' → Sheets RGB dict (0-1 range)."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return {"red": r / 255, "green": g / 255, "blue": b / 255}


TAB_COLORS = {
    "YouTube Sources":   "#ea4335",   # YouTube red
    "Instagram Sources": "#c13584",   # Instagram purple
    "LinkedIn Sources":  "#0077b5",   # LinkedIn blue
    "Linked Sources":    "#f59e0b",   # Amber — websites/newsletters/podcasts
    "New Inputs":        "#34a853",   # Green
    "Pipeline Status":   "#4285f4",   # Google blue
}


# ── Existing DB sources ──────────────────────────────────────────────────────

def load_existing_sources() -> dict[str, list]:
    """Pull any existing tracked_sources rows from the local DB."""
    try:
        sys.path.insert(0, str(BASE_DIR))
        import database as db
        conn = db.get_connection()
        rows = conn.execute("SELECT * FROM tracked_sources ORDER BY platform, source_name").fetchall()
        conn.close()
    except Exception:
        return {}

    mapping = {
        "youtube":   "YouTube Sources",
        "instagram": "Instagram Sources",
        "linkedin":  "LinkedIn Sources",
        "web":       "Linked Sources",
    }
    extra: dict[str, list] = {}
    for r in rows:
        tab = mapping.get((r["platform"] or "").lower())
        if tab:
            extra.setdefault(tab, []).append([
                r["source_name"] or "",
                r["source_url"]  or "",
                r["source_type"] or "competitor",
                (r["added_on"]   or datetime.now().strftime("%Y-%m-%d"))[:10],
                "Active",
                "",
                "",
                r["notes"] or "",
            ])
    return extra


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    print("\n" + "=" * 60)
    print("  SOURCES SHEET SETUP")
    print("=" * 60)
    print("  Authenticating with Google (browser will open)...\n")

    service = get_oauth_service()

    # ── Step 1: Create spreadsheet ────────────────────────────────────────────
    print("  Creating spreadsheet...")
    first_tab = list(TABS.keys())[0]
    body = {
        "properties": {"title": SHEET_NAME},
        "sheets": [{"properties": {"title": first_tab}}],
    }
    spreadsheet = service.spreadsheets().create(body=body).execute()
    sheet_id    = spreadsheet["spreadsheetId"]
    sheet_url   = f"https://docs.google.com/spreadsheets/d/{sheet_id}/edit"
    print(f"  Created: {sheet_url}\n")

    first_sheet_gid = spreadsheet["sheets"][0]["properties"]["sheetId"]

    # ── Step 2: Add remaining tabs ────────────────────────────────────────────
    add_requests = []
    for tab_name in list(TABS.keys())[1:]:
        add_requests.append({
            "addSheet": {
                "properties": {
                    "title": tab_name,
                    "tabColor": hex_to_rgb(TAB_COLORS[tab_name]),
                }
            }
        })

    # Also colour the first tab
    add_requests.append({
        "updateSheetProperties": {
            "properties": {
                "sheetId": first_sheet_gid,
                "title": first_tab,
                "tabColor": hex_to_rgb(TAB_COLORS[first_tab]),
            },
            "fields": "tabColor,title",
        }
    })

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": add_requests}
    ).execute()
    print("  Tabs created.")

    # Re-fetch to get all sheet GIDs
    meta       = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
    gid_map    = {s["properties"]["title"]: s["properties"]["sheetId"]
                  for s in meta["sheets"]}

    # ── Step 3: Write headers + data ─────────────────────────────────────────
    existing_db_rows = load_existing_sources()

    for tab_name, headers in TABS.items():
        rows     = list(headers)                       # header row
        starter  = STARTER_ROWS.get(tab_name, [])
        db_extra = existing_db_rows.get(tab_name, [])

        # Deduplicate by URL/Name — prefer DB rows over starters
        seen_names = {r[0] for r in db_extra}
        for s in starter:
            if s[0] not in seen_names:
                rows.append(s)
                seen_names.add(s[0])
        rows.extend(db_extra)

        if len(rows) > 1:  # has data beyond header
            service.spreadsheets().values().update(
                spreadsheetId=sheet_id,
                range=f"'{tab_name}'!A1",
                valueInputOption="RAW",
                body={"values": rows},
            ).execute()

        print(f"  {tab_name}: {len(rows)-1} row(s) written")

    # ── Step 4: Format header rows ────────────────────────────────────────────
    format_requests = []
    for tab_name, gid in gid_map.items():
        format_requests.append({
            "repeatCell": {
                "range": {"sheetId": gid, "startRowIndex": 0, "endRowIndex": 1},
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": hex_to_rgb("#1e293b"),
                        "textFormat": {
                            "foregroundColor": hex_to_rgb("#f8fafc"),
                            "bold": True,
                            "fontSize": 10,
                        }
                    }
                },
                "fields": "userEnteredFormat(backgroundColor,textFormat)",
            }
        })
        # Freeze header row
        format_requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": gid,
                    "gridProperties": {"frozenRowCount": 1},
                },
                "fields": "gridProperties.frozenRowCount",
            }
        })

    service.spreadsheets().batchUpdate(
        spreadsheetId=sheet_id,
        body={"requests": format_requests}
    ).execute()
    print("  Headers formatted.")

    # ── Step 5: Print service account email to share with ────────────────────
    sa_json_path = BASE_DIR / "service_account.json"
    sa_email     = "(unknown — check your service_account.json)"
    if sa_json_path.exists():
        try:
            sa_data  = json.loads(sa_json_path.read_text())
            sa_email = sa_data.get("client_email", sa_email)
        except Exception:
            pass

    # ── Done ─────────────────────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  ✓  SETUP COMPLETE")
    print("=" * 60)
    print(f"\n  Sheet URL : {sheet_url}")
    print(f"  Sheet ID  : {sheet_id}")
    print()
    print("  NEXT STEPS — do these now:")
    print()
    print(f"  1. Share the sheet with the service account as EDITOR:")
    print(f"       {sa_email}")
    print(f"     (Open the sheet → Share → paste the email → Editor → Send)")
    print()
    print(f"  2. Add SOURCES_SHEET_ID to GitHub Secrets:")
    print(f"       Name  : SOURCES_SHEET_ID")
    print(f"       Value : {sheet_id}")
    print()
    print(f"  3. Add to your local .env file:")
    print(f"       SOURCES_SHEET_ID={sheet_id}")
    print()
    print(f"  4. Enable Google Sheets API in Google Cloud Console")
    print(f"     (if not already enabled)")
    print(f"     https://console.cloud.google.com/apis/library/sheets.googleapis.com")
    print()
    print("  5. Fill in your channel URLs in the YouTube Sources tab,")
    print("     then let the next pipeline run do the rest.")
    print()

    # Save sheet_id to a local file so source_manager can pick it up
    # even before the env var is set
    id_file = BASE_DIR / ".sources_sheet_id"
    id_file.write_text(sheet_id)
    print(f"  (Sheet ID also saved to .sources_sheet_id for local use)")
    print()


if __name__ == "__main__":
    main()
