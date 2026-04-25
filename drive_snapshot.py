"""
Drive-to-Drive Snapshot (Cloud-side backup)
--------------------------------------------
Runs entirely in the cloud (GitHub Actions).
Copies files from Drive data/ folder into backups/<date>/
using a Service Account — no laptop, no browser auth needed.

Usage (local test):
  python drive_snapshot.py

In GitHub Actions, set secret GDRIVE_SERVICE_ACCOUNT_JSON
with the full contents of your service account key JSON file.
"""

import os
import sys
import json
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build

DRIVE_FOLDER_ID = "1tnU4bjFcLVdXx57yARwU__PVVvWoWPD1"
SCOPES          = ["https://www.googleapis.com/auth/drive"]


def get_service():
    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        # Fall back to a local key file for testing
        local_key = os.path.join(os.path.dirname(__file__), "service_account.json")
        if os.path.exists(local_key):
            with open(local_key) as f:
                sa_json = f.read()
        else:
            print("[!] GDRIVE_SERVICE_ACCOUNT_JSON env var not set and service_account.json not found.")
            sys.exit(1)

    info  = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def get_folder_id(service, name: str, parent_id: str) -> str | None:
    query = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
        f" and '{parent_id}' in parents and trashed=false"
    )
    results = service.files().list(q=query, fields="files(id)").execute()
    files = results.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_folder(service, name: str, parent_id: str) -> str:
    fid = get_folder_id(service, name, parent_id)
    if fid:
        return fid
    meta = {
        "name":     name,
        "mimeType": "application/vnd.google-apps.folder",
        "parents":  [parent_id],
    }
    folder = service.files().create(body=meta, fields="id").execute()
    return folder["id"]


def copy_file(service, file_id: str, dest_folder_id: str, name: str) -> str:
    """Copy a file to dest_folder, preserving name."""
    body = {"name": name, "parents": [dest_folder_id]}
    copied = service.files().copy(fileId=file_id, body=body, fields="id").execute()
    return copied["id"]


def list_files_in_folder(service, folder_id: str) -> list[dict]:
    query  = f"'{folder_id}' in parents and trashed=false and mimeType != 'application/vnd.google-apps.folder'"
    fields = "files(id,name,modifiedTime,size)"
    items  = service.files().list(q=query, fields=fields).execute()
    return items.get("files", [])


def main():
    print(f"  Drive Snapshot — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"  Authenticating with service account...")
    service = get_service()

    today = datetime.now().strftime("%Y-%m-%d")

    # Locate data/ folder inside the root Drive folder
    data_folder_id = get_folder_id(service, "data", DRIVE_FOLDER_ID)
    if not data_folder_id:
        print("  [!] data/ folder not found in Drive root. Run gdrive_backup.py migrate first.")
        sys.exit(1)

    # Get or create backups/<date>/ folder
    backups_folder_id = get_or_create_folder(service, "backups", DRIVE_FOLDER_ID)
    dated_folder_id   = get_or_create_folder(service, today, backups_folder_id)

    # List files in data/
    files = list_files_in_folder(service, data_folder_id)
    if not files:
        print("  [!] No files found in data/ folder.")
        sys.exit(0)

    print(f"  Snapshotting {len(files)} file(s) from data/ -> backups/{today}/")
    copied = 0
    for f in files:
        try:
            copy_file(service, f["id"], dated_folder_id, f["name"])
            size_kb = int(f.get("size", 0)) // 1024
            print(f"    Copied: {f['name']}  ({size_kb}KB)")
            copied += 1
        except Exception as e:
            print(f"    [warn] Could not copy {f['name']}: {e}")

    print(f"\n  Snapshot complete: {copied}/{len(files)} files")
    print(f"  Location: backups/{today}/")
    print(f"  https://drive.google.com/drive/folders/{DRIVE_FOLDER_ID}")


if __name__ == "__main__":
    main()
