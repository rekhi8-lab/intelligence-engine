"""
Google Drive Backup & Migration
---------------------------------
Syncs the Intelligence Engine project to Google Drive.

First-time use (migration):
  python gdrive_backup.py migrate     # uploads all project files

Periodic backup (run manually or via Task Scheduler):
  python gdrive_backup.py backup      # snapshots DB + outputs to backups/<date>/

Status check:
  python gdrive_backup.py status      # shows what's in the Drive folder

SETUP (one-time):
  1. Go to https://console.cloud.google.com
  2. Create a project (or use existing one)
  3. Enable "Google Drive API"
  4. Go to APIs & Services > Credentials
  5. Create credential > OAuth client ID > Desktop app
  6. Download JSON > save as credentials.json in this folder
  7. Run: python gdrive_backup.py migrate
  8. A browser window opens — sign in and allow access
  9. token.json is created automatically for future runs
"""

import os
import sys
import json
import mimetypes
from pathlib import Path
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

BASE_DIR       = Path(__file__).parent
CREDS_FILE     = BASE_DIR / "credentials.json"
TOKEN_FILE     = BASE_DIR / "token.json"
SCOPES         = ["https://www.googleapis.com/auth/drive"]
DRIVE_FOLDER_ID = "1tnU4bjFcLVdXx57yARwU__PVVvWoWPD1"

# Files and folders to include in migration/backup
PROJECT_FILES = [
    "run_all.py",
    "listener_brain.py",
    "query_engine.py",
    "database.py",
    "main.py",
    "youtube_scraper.py",
    "thumbnail_analyzer.py",
    "transcript_analyzer.py",
    "content_generator.py",
    "chatbot_server.py",
    "chatbot_test.html",
    "whatsapp_import.py",
    "auditor.py",
    "migrate.py",
    "gdrive_backup.py",
    "knowledge_base.txt",
    "requirements.txt",
    ".env.example",
    ".gitignore",
    "text_analyzer.py",
    "config.py",
]

# Data files included in every backup snapshot
BACKUP_FILES = [
    "intelligence.json",
    "intelligence.db",
    "memory.json",
]


# ─────────────────────────────────────────────────────────────
# AUTH
# ─────────────────────────────────────────────────────────────

def get_service():
    creds = None

    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not CREDS_FILE.exists():
                print("\n  [!] credentials.json not found.")
                print("  Follow the setup steps at the top of gdrive_backup.py")
                print("  then place credentials.json in the Trend Scraper folder.\n")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDS_FILE), SCOPES)
            creds = flow.run_local_server(port=0)

        TOKEN_FILE.write_text(creds.to_json())

    return build("drive", "v3", credentials=creds)


# ─────────────────────────────────────────────────────────────
# DRIVE HELPERS
# ─────────────────────────────────────────────────────────────

def get_or_create_folder(service, name: str, parent_id: str) -> str:
    """Return folder ID, creating it if it doesn't exist."""
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id,name)").execute()
    files = results.get("files", [])
    if files:
        return files[0]["id"]

    meta = {
        "name":     name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents":  [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def file_exists_in_folder(service, filename: str, folder_id: str):
    """Return file metadata if file exists in folder, else None."""
    query = f"name='{filename}' and '{folder_id}' in parents and trashed=false"
    results = service.files().list(q=query, fields="files(id,name,modifiedTime)").execute()
    files = results.get("files", [])
    return files[0] if files else None


def upload_file(service, local_path: Path, folder_id: str, overwrite: bool = True) -> str:
    """Upload or update a file. Returns the Drive file ID."""
    mime, _ = mimetypes.guess_type(str(local_path))
    mime = mime or "application/octet-stream"

    existing = file_exists_in_folder(service, local_path.name, folder_id)
    media = MediaFileUpload(str(local_path), mimetype=mime, resumable=True)

    if existing and overwrite:
        f = service.files().update(
            fileId=existing["id"],
            media_body=media,
            fields="id"
        ).execute()
        return f["id"]
    else:
        meta = {"name": local_path.name, "parents": [folder_id]}
        f = service.files().create(
            body=meta,
            media_body=media,
            fields="id"
        ).execute()
        return f["id"]


def upload_folder_contents(service, local_dir: Path, drive_folder_id: str, label: str = ""):
    """Recursively upload a local directory to a Drive folder."""
    uploaded = 0
    for item in sorted(local_dir.iterdir()):
        if item.name.startswith("."):
            continue
        if item.is_dir():
            sub_id = get_or_create_folder(service, item.name, drive_folder_id)
            uploaded += upload_folder_contents(service, item, sub_id)
        elif item.is_file():
            try:
                upload_file(service, item, drive_folder_id)
                print(f"  {'[' + label + '] ' if label else ''}Uploaded: {item.name}")
                uploaded += 1
            except Exception as e:
                print(f"  [warn] Could not upload {item.name}: {e}")
    return uploaded


# ─────────────────────────────────────────────────────────────
# MIGRATE — first-time full upload
# ─────────────────────────────────────────────────────────────

def cmd_migrate():
    print("\n  Authenticating with Google Drive...")
    service = get_service()

    print(f"  Target folder ID: {DRIVE_FOLDER_ID}")
    print("  Creating folder structure...")

    code_folder    = get_or_create_folder(service, "code",    DRIVE_FOLDER_ID)
    data_folder    = get_or_create_folder(service, "data",    DRIVE_FOLDER_ID)
    output_folder  = get_or_create_folder(service, "output",  DRIVE_FOLDER_ID)
    backups_folder = get_or_create_folder(service, "backups", DRIVE_FOLDER_ID)

    print("  Folders ready: code / data / output / backups")
    print()

    # Upload code files
    print("  Uploading code files...")
    code_count = 0
    for fname in PROJECT_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, code_folder)
            print(f"    {fname}")
            code_count += 1
        else:
            print(f"    [skip] {fname} (not found)")

    # Upload data files
    print(f"\n  Uploading data files...")
    data_count = 0
    for fname in BACKUP_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, data_folder)
            print(f"    {fname}")
            data_count += 1

    # Upload output folder
    output_local = BASE_DIR / "output"
    if output_local.exists():
        print(f"\n  Uploading output folder...")
        out_count = upload_folder_contents(service, output_local, output_folder, "output")
        print(f"    {out_count} files uploaded")

    # Upload handoff document
    handoff = Path(r"C:\Users\Acer\Desktop\Claude Code handoff documents for Multilayer architecture system build\CLAUDE_CODE_HANDOFF.md")
    if handoff.exists():
        upload_file(service, handoff, DRIVE_FOLDER_ID)
        print(f"\n  Uploaded: CLAUDE_CODE_HANDOFF.md")

    print(f"\n  Migration complete.")
    print(f"  Code: {code_count} files | Data: {data_count} files")
    print(f"  View at: https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}")


# ─────────────────────────────────────────────────────────────
# BACKUP — dated snapshot of DB + outputs
# ─────────────────────────────────────────────────────────────

def cmd_backup():
    print("\n  Authenticating with Google Drive...")
    service = get_service()

    today = datetime.now().strftime("%Y-%m-%d")
    ts    = datetime.now().strftime("%Y-%m-%d_%H-%M")

    backups_folder = get_or_create_folder(service, "backups", DRIVE_FOLDER_ID)
    dated_folder   = get_or_create_folder(service, today,     backups_folder)
    data_folder    = get_or_create_folder(service, "data",    DRIVE_FOLDER_ID)
    code_folder    = get_or_create_folder(service, "code",    DRIVE_FOLDER_ID)

    print(f"  Backup snapshot: {ts}")
    count = 0

    # Snapshot data files into backups/<date>/
    for fname in BACKUP_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, dated_folder, overwrite=False)
            print(f"    Snapshot: {fname}")
            count += 1

    # Also update the live data/ folder with latest versions
    for fname in BACKUP_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, data_folder, overwrite=True)

    # Sync latest output/drafts
    drafts_local = BASE_DIR / "output" / "drafts"
    if drafts_local.exists():
        output_folder = get_or_create_folder(service, "output",  DRIVE_FOLDER_ID)
        drafts_drive  = get_or_create_folder(service, "drafts",  output_folder)
        new_files = upload_folder_contents(service, drafts_local, drafts_drive)
        if new_files:
            print(f"    Synced {new_files} draft file(s) to output/drafts/")

    # Sync latest code (catches any edits)
    for fname in PROJECT_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, code_folder, overwrite=True)

    print(f"\n  Backup complete: {count} data files + code synced")
    print(f"  Snapshot at: backups/{today}/")
    print(f"  https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}")


# ─────────────────────────────────────────────────────────────
# STATUS — list Drive folder contents
# ─────────────────────────────────────────────────────────────

def cmd_status():
    print("\n  Authenticating with Google Drive...")
    service = get_service()

    def list_folder(folder_id: str, indent: int = 0):
        query  = f"'{folder_id}' in parents and trashed=false"
        fields = "files(id,name,mimeType,modifiedTime,size)"
        items  = service.files().list(q=query, fields=fields, orderBy="name").execute().get("files", [])
        for item in items:
            is_folder = item["mimeType"] == "application/vnd.google-apps.folder"
            size = f"  {int(item.get('size', 0)) // 1024}KB" if not is_folder and item.get("size") else ""
            mod  = item.get("modifiedTime", "")[:10]
            print(f"  {'  ' * indent}{'[DIR] ' if is_folder else '      '}{item['name']:<40} {mod}{size}")
            if is_folder and indent < 1:
                list_folder(item["id"], indent + 1)

    print(f"\n  Google Drive folder contents:")
    print(f"  https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}\n")
    list_folder(DRIVE_FOLDER_ID)


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Google Drive backup for Intelligence Engine")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("migrate", help="First-time migration of all project files")
    sub.add_parser("backup",  help="Periodic backup snapshot to Drive")
    sub.add_parser("status",  help="Show Drive folder contents")
    args = parser.parse_args()

    if args.command == "migrate":
        cmd_migrate()
    elif args.command == "backup":
        cmd_backup()
    elif args.command == "status":
        cmd_status()
    else:
        parser.print_help()
