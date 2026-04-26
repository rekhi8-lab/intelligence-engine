"""
Cloud Sync — Drive <-> local (ephemeral runner)
-------------------------------------------------
Used by GitHub Actions to:
  download  Pull intelligence.db + data files from Drive before pipeline run
  upload    Push intelligence.db + output files back to Drive after run

Auth: Google Service Account via GDRIVE_SERVICE_ACCOUNT_JSON env var.
"""

import os
import sys
import json
import io
from pathlib import Path
from datetime import datetime

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload

DRIVE_FOLDER_ID = "1tnU4bjFcLVdXx57yARwU__PVVvWoWPD1"
SCOPES          = ["https://www.googleapis.com/auth/drive"]
BASE_DIR        = Path(__file__).parent

DATA_FILES   = ["intelligence.db", "intelligence.json", "memory.json"]

# Fixed-name output files the service account can UPDATE each run.
# Service accounts have no storage quota and cannot CREATE new files
# in a personal Drive — only update files that already exist (owned
# by the user's Google account).  Seed these once from the laptop
# with: python gdrive_backup.py migrate
# Structure: (local relative path, Drive subfolder under "output/")
FIXED_OUTPUT_FILES = [
    ("output/drafts/latest_drafts.txt",               "drafts"),
    ("output/guidance/latest_brief.txt",              "guidance"),
    ("output/guidance/latest_brief.json",             "guidance"),
    ("output/prompts/system_requests.txt",            "prompts"),
    ("output/prompts/priority.txt",                   "prompts"),
    ("output/founder_signals/latest_signals.txt",     "founder_signals"),
]

# Input files that the user writes locally (via nudge_ui.py) and
# that need to reach the cloud pipeline via Drive
FIXED_INPUT_FILES = [
    ("inputs/manual/insights.txt",  "manual"),   # text/links from nudge UI
]

# Drive folder name that holds user-supplied input files
INPUTS_FOLDER = "inputs"


# ── auth ──────────────────────────────────────────────────────

def get_service():
    sa_json = os.environ.get("GDRIVE_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        local = BASE_DIR / "service_account.json"
        if local.exists():
            sa_json = local.read_text()
        else:
            print("[!] GDRIVE_SERVICE_ACCOUNT_JSON not set.")
            sys.exit(1)
    info  = json.loads(sa_json)
    creds = service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


# ── helpers ───────────────────────────────────────────────────

def find_file(service, name: str, parent_id: str) -> dict | None:
    q = f"name='{name}' and '{parent_id}' in parents and trashed=false"
    r = service.files().list(q=q, fields="files(id,name,size)").execute()
    files = r.get("files", [])
    return files[0] if files else None


def find_folder(service, name: str, parent_id: str) -> str | None:
    q = (f"name='{name}' and mimeType='application/vnd.google-apps.folder'"
         f" and '{parent_id}' in parents and trashed=false")
    r = service.files().list(q=q, fields="files(id)").execute()
    files = r.get("files", [])
    return files[0]["id"] if files else None


def get_or_create_folder(service, name: str, parent_id: str) -> str:
    fid = find_folder(service, name, parent_id)
    if fid:
        return fid
    meta = {"name": name, "mimeType": "application/vnd.google-apps.folder", "parents": [parent_id]}
    return service.files().create(body=meta, fields="id").execute()["id"]


def download_folder(service, drive_folder_id: str, local_dir: Path):
    """Recursively download a Drive folder to a local directory."""
    local_dir.mkdir(parents=True, exist_ok=True)
    q     = f"'{drive_folder_id}' in parents and trashed=false"
    items = service.files().list(q=q, fields="files(id,name,mimeType)").execute().get("files", [])
    count = 0
    for item in items:
        if item["mimeType"] == "application/vnd.google-apps.folder":
            sub_local = local_dir / item["name"]
            count    += download_folder(service, item["id"], sub_local)
        else:
            dest = local_dir / item["name"]
            download_file(service, item["id"], dest)
            print(f"  Downloaded input: {dest.relative_to(dest.parents[3] if dest.parents[3].exists() else dest.parent)}")
            count += 1
    return count


def download_file(service, file_id: str, dest: Path):
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    dest.write_bytes(buf.getvalue())


def upload_file(service, local_path: Path, folder_id: str):
    import mimetypes
    mime, _ = mimetypes.guess_type(str(local_path))
    mime = mime or "application/octet-stream"
    # Use resumable only for large files (>5MB); small files upload more
    # reliably without it — avoids silent failures in CI environments
    size      = local_path.stat().st_size
    resumable = size > 5 * 1024 * 1024
    existing  = find_file(service, local_path.name, folder_id)
    media     = MediaFileUpload(str(local_path), mimetype=mime, resumable=resumable)
    if existing:
        service.files().update(fileId=existing["id"], media_body=media).execute()
    else:
        meta = {"name": local_path.name, "parents": [folder_id]}
        service.files().create(body=meta, media_body=media).execute()


def upload_folder(service, local_dir: Path, drive_folder_id: str):
    count = 0
    for item in sorted(local_dir.rglob("*")):
        if item.is_file() and not item.name.startswith("."):
            rel   = item.relative_to(local_dir)
            parts = list(rel.parts)
            folder_id = drive_folder_id
            for part in parts[:-1]:
                folder_id = get_or_create_folder(service, part, folder_id)
            try:
                upload_file(service, item, folder_id)
                print(f"  Uploaded: {rel}")
                count += 1
            except Exception as e:
                print(f"  [UPLOAD FAILED] {rel}: {e}")
    return count


# ── commands ──────────────────────────────────────────────────

def cmd_download():
    """Pull data files from Drive to local (before pipeline run)."""
    print("=== CLOUD SYNC: DOWNLOAD ===")
    service = get_service()

    data_folder_id = find_folder(service, "data", DRIVE_FOLDER_ID)
    if not data_folder_id:
        print("  [!] data/ folder not in Drive. First run — starting fresh.")
        return

    for fname in DATA_FILES:
        f = find_file(service, fname, data_folder_id)
        if f:
            dest = BASE_DIR / fname
            download_file(service, f["id"], dest)
            size_kb = int(f.get("size", 0)) // 1024
            print(f"  Downloaded: {fname}  ({size_kb}KB)")
        else:
            print(f"  [skip] {fname} not in Drive yet")

    # Download inputs/ folder (user-uploaded personal inbox, etc.)
    inputs_folder_id = find_folder(service, INPUTS_FOLDER, DRIVE_FOLDER_ID)
    if inputs_folder_id:
        local_inputs = BASE_DIR / "inputs"
        n = download_folder(service, inputs_folder_id, local_inputs)
        print(f"  Downloaded {n} input file(s) from Drive inputs/")
    else:
        print("  [skip] No inputs/ folder in Drive yet — upload files to create it")

    print("  Download complete.\n")


def cmd_upload():
    """Push data + output files from local back to Drive (after pipeline run)."""
    print("=== CLOUD SYNC: UPLOAD ===")
    service = get_service()

    data_folder_id   = get_or_create_folder(service, "data",    DRIVE_FOLDER_ID)
    output_folder_id = get_or_create_folder(service, "output",  DRIVE_FOLDER_ID)
    backups_id       = get_or_create_folder(service, "backups", DRIVE_FOLDER_ID)

    # Upload data files
    for fname in DATA_FILES:
        fpath = BASE_DIR / fname
        if fpath.exists():
            upload_file(service, fpath, data_folder_id)
            print(f"  Uploaded: {fname}")

    # Upload fixed-name output files only.
    # Timestamped files (e.g. drafts_run5_2026-04-26...txt) are skipped —
    # the service account cannot CREATE new files (no storage quota).
    # Use: python gdrive_backup.py migrate  to seed these files once via
    # OAuth, then this block updates them every run.
    synced = 0
    for rel_path, subfolder_name in FIXED_OUTPUT_FILES:
        local_path = BASE_DIR / rel_path
        if not local_path.exists():
            print(f"  [skip] {rel_path} (not created yet this run)")
            continue
        subfolder_id = get_or_create_folder(service, subfolder_name, output_folder_id)
        try:
            upload_file(service, local_path, subfolder_id)
            print(f"  Uploaded: {rel_path}")
            synced += 1
        except Exception as e:
            print(f"  [UPLOAD FAILED] {rel_path}: {e}")
    print(f"  Output: {synced}/{len(FIXED_OUTPUT_FILES)} fixed files synced")

    # Upload user-written input files (nudge UI text → Drive → cloud pipeline)
    inputs_folder_id = get_or_create_folder(service, "inputs", DRIVE_FOLDER_ID)
    for rel_path, subfolder_name in FIXED_INPUT_FILES:
        local_path = BASE_DIR / rel_path
        if not local_path.exists() or local_path.stat().st_size == 0:
            continue   # nothing to upload (empty or missing)
        subfolder_id = get_or_create_folder(service, subfolder_name, inputs_folder_id)
        try:
            upload_file(service, local_path, subfolder_id)
            print(f"  Uploaded input: {rel_path}")
        except Exception as e:
            print(f"  [UPLOAD FAILED] {rel_path}: {e}")

    # Snapshot data/ into backups/<date>/
    today       = datetime.now().strftime("%Y-%m-%d")
    dated_id    = get_or_create_folder(service, today, backups_id)
    snap_count  = 0
    for fname in DATA_FILES:
        f = find_file(service, fname, data_folder_id)
        if f:
            try:
                body = {"name": fname, "parents": [dated_id]}
                service.files().copy(fileId=f["id"], body=body).execute()
                snap_count += 1
            except Exception as e:
                print(f"  [warn] snapshot {fname}: {e}")
    print(f"  Snapshot: {snap_count} files -> backups/{today}/")

    print("  Upload complete.\n")


# ── main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "download":
        cmd_download()
    elif cmd == "upload":
        cmd_upload()
    else:
        print("Usage: python cloud_sync.py download|upload")
        sys.exit(1)
