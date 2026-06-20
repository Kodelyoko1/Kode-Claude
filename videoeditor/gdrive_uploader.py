"""
Google Drive uploader for VideoEditor using a service account.

Setup (one-time):
  1. Share your Google Drive output folder with:
     video-uploader@noble-cubist-489919-q7.iam.gserviceaccount.com
     (give it Editor access)
  2. Set GDRIVE_FOLDER_ID in .env to the folder ID from the Drive URL

Env vars:
  GDRIVE_FOLDER_ID   — Drive folder ID to upload into (required)
  GDRIVE_SA_KEY      — path to service account JSON (default: data/gdrive_service_account.json)
"""

import os
from pathlib import Path

SA_KEY = Path(os.getenv("GDRIVE_SA_KEY", "data/gdrive_service_account.json"))
SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _build_service():
    try:
        from google.oauth2 import service_account
        from google.auth.transport.requests import Request as GRequest
        from googleapiclient.discovery import build
        import googleapiclient.discovery
    except ImportError:
        return None, "pip install google-api-python-client google-auth requests"

    if not SA_KEY.exists():
        return None, f"Service account key not found at {SA_KEY}"

    creds = service_account.Credentials.from_service_account_file(
        str(SA_KEY), scopes=SCOPES
    )
    creds.refresh(GRequest())

    # Use requests-based authorized session to avoid httplib2 SSL issues
    import requests
    from google.auth.transport.requests import AuthorizedSession
    authed = AuthorizedSession(creds)

    service = build("drive", "v3", credentials=creds,
                    requestBuilder=None,
                    http=None)
    # Patch with requests transport
    from googleapiclient.http import build_http
    service = build("drive", "v3", credentials=creds)
    return service, None


def upload_file(local_path: str, filename: str | None = None, folder_id: str | None = None) -> dict:
    """Upload a single file to Google Drive. Returns {status, file_id, url}."""
    service, err = _build_service()
    if err:
        return {"error": err}

    folder_id = folder_id or os.getenv("GDRIVE_FOLDER_ID")
    if not folder_id:
        return {"error": "Set GDRIVE_FOLDER_ID in .env or pass folder_id"}

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError:
        return {"error": "pip install google-api-python-client"}

    path = Path(local_path)
    name = filename or path.name

    # Detect MIME type
    suffix = path.suffix.lower()
    mime = {
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".json": "application/json",
        ".txt": "text/plain",
        ".md": "text/markdown",
    }.get(suffix, "application/octet-stream")

    metadata = {"name": name, "parents": [folder_id]}
    media = MediaFileUpload(str(path), mimetype=mime, resumable=True)

    try:
        f = service.files().create(
            body=metadata, media_body=media, fields="id,name,webViewLink"
        ).execute()
        return {
            "status": "uploaded",
            "file_id": f["id"],
            "name": f["name"],
            "url": f.get("webViewLink", f"https://drive.google.com/file/d/{f['id']}/view"),
        }
    except Exception as exc:
        return {"error": f"Upload failed: {exc}"}


def upload_video_outputs(meta: dict, folder_id: str | None = None) -> dict:
    """
    Upload all VideoEditor outputs for a processed video to Google Drive.
    meta — dict returned by videoeditor.tools.process_video()
    Returns {"uploads": [...per-file result dicts...]}
    """
    uploads = []

    files_to_upload = [meta["master"]]
    for reel in meta.get("reels", []):
        files_to_upload.append(reel["file"])

    # Also upload meta JSON
    slug = meta["slug"]
    meta_json = str(Path(meta["master"]).parent / f"{slug}_meta.json")
    if Path(meta_json).exists():
        files_to_upload.append(meta_json)

    for f in files_to_upload:
        if Path(f).exists():
            r = upload_file(f, folder_id=folder_id)
            r["local_file"] = f
            uploads.append(r)

    return {"uploads": uploads}
