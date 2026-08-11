"""
Google Drive uploader for VideoEditor using a service account + requests transport.

Setup (one-time):
  1. Enable Google Drive API in Cloud Console
  2. Share your Drive output folder with:
     video-uploader@noble-cubist-489919-q7.iam.gserviceaccount.com  (Editor)
  3. Set GDRIVE_FOLDER_ID in .env (or it's hardcoded in run_videoeditor_auto.py)

Env vars:
  GDRIVE_FOLDER_ID   — Drive folder ID to upload into
  GDRIVE_SA_KEY      — path to service account JSON (default: data/gdrive_service_account.json)
"""

import os
from pathlib import Path

SA_KEY = Path(os.getenv("GDRIVE_SA_KEY", "data/gdrive_service_account.json"))
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_token() -> str:
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as GRequest

    creds = service_account.Credentials.from_service_account_file(
        str(SA_KEY), scopes=SCOPES
    )
    creds.refresh(GRequest())
    return creds.token


def upload_file(local_path: str, filename: str | None = None, folder_id: str | None = None) -> dict:
    """Upload a single file to Google Drive. Returns {status, file_id, url}."""
    import requests

    if not SA_KEY.exists():
        return {"error": f"Service account key not found at {SA_KEY}"}

    folder_id = folder_id or os.getenv("GDRIVE_FOLDER_ID")
    if not folder_id:
        return {"error": "Set GDRIVE_FOLDER_ID in .env or pass folder_id"}

    path = Path(local_path)
    if not path.exists():
        return {"error": f"File not found: {local_path}"}

    name = filename or path.name
    suffix = path.suffix.lower()
    mime = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".json": "application/json",
        ".txt":  "text/plain",
        ".md":   "text/markdown",
    }.get(suffix, "application/octet-stream")

    try:
        token = _get_token()
    except Exception as exc:
        return {"error": f"Auth failed: {exc}"}

    headers = {"Authorization": f"Bearer {token}"}

    # Resumable upload for large files
    init_headers = {
        **headers,
        "Content-Type": "application/json",
        "X-Upload-Content-Type": mime,
        "X-Upload-Content-Length": str(path.stat().st_size),
    }
    meta = {"name": name, "parents": [folder_id]}

    try:
        init = requests.post(
            "https://www.googleapis.com/upload/drive/v3/files?uploadType=resumable",
            headers=init_headers,
            json=meta,
            timeout=30,
        )
        if init.status_code != 200:
            return {"error": f"Upload init failed {init.status_code}: {init.text[:200]}"}

        upload_url = init.headers["Location"]

        with open(path, "rb") as f:
            data = f.read()

        up = requests.put(
            upload_url,
            headers={**headers, "Content-Type": mime},
            data=data,
            timeout=600,
        )
        if up.status_code not in (200, 201):
            return {"error": f"Upload failed {up.status_code}: {up.text[:200]}"}

        file_id = up.json().get("id")
        return {
            "status": "uploaded",
            "file_id": file_id,
            "name": name,
            "url": f"https://drive.google.com/file/d/{file_id}/view",
        }

    except Exception as exc:
        return {"error": f"Upload exception: {exc}"}


def upload_video_outputs(meta: dict, folder_id: str | None = None) -> dict:
    """
    Upload all VideoEditor outputs for a processed video to Google Drive.
    meta — dict returned by videoeditor.tools.process_video()
    """
    uploads = []

    files_to_upload = [meta["master"]]
    for reel in meta.get("reels", []):
        files_to_upload.append(reel["file"])

    slug = meta["slug"]
    meta_json = str(Path(meta["master"]).parent / f"{slug}_meta.json")
    if Path(meta_json).exists():
        files_to_upload.append(meta_json)

    for f in files_to_upload:
        if Path(f).exists():
            r = upload_file(f, folder_id=folder_id)
            r["local_file"] = f
            uploads.append(r)
            status = "✓" if r.get("status") == "uploaded" else "✗"
            print(f"  [{status}] {Path(f).name} → {r.get('url', r.get('error'))}")

    return {"uploads": uploads}
