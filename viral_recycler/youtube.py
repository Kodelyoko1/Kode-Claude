"""
YouTube uploader using the official Data API v3.

One-time setup (required because no other path is allowed by YouTube):
  1. Go to console.cloud.google.com → create a project
  2. Enable "YouTube Data API v3"
  3. Create OAuth 2.0 Client ID (type: Desktop)
  4. Download credentials JSON to data/yt_client_secrets.json
  5. Run setup_viral_recycler.py — this opens a browser to authorize, then
     stores a refresh token at data/yt_token.json
  6. After that, uploads run unattended.

Quota note: YouTube Data API gives ~10,000 units/day per project.
An upload costs 1,600 units. Hard cap = 6 uploads/day per project.
"""
import json
from datetime import datetime
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
CLIENT_SECRETS = DATA_DIR / "yt_client_secrets.json"
TOKEN_PATH = DATA_DIR / "yt_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def _build_service():
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError as e:
        return None, f"missing dependency: {e}. Run: pip install google-api-python-client google-auth-oauthlib"

    if not TOKEN_PATH.exists():
        return None, "no_token: run setup_viral_recycler.py first to authorize"

    creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        TOKEN_PATH.write_text(creds.to_json())

    return build("youtube", "v3", credentials=creds), None


def authorize_interactive() -> dict:
    """Open browser to do the OAuth flow once. Saves a refresh token."""
    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        return {"error": "pip install google-auth-oauthlib"}
    if not CLIENT_SECRETS.exists():
        return {"error": f"put OAuth client secret at {CLIENT_SECRETS}"}
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRETS), SCOPES)
    creds = flow.run_local_server(port=0)
    TOKEN_PATH.write_text(creds.to_json())
    return {"status": "authorized", "token_path": str(TOKEN_PATH)}


def upload(
    video_path: str,
    title: str,
    description: str,
    tags: list,
    privacy: str = "public",
    category_id: str = "22",  # 22 = People & Blogs (best default for Shorts)
    notify_subscribers: bool = True,
) -> dict:
    """Upload a video to the authorized channel. Returns video_id or error."""
    service, err = _build_service()
    if err:
        return {"error": err}

    try:
        from googleapiclient.http import MediaFileUpload
    except ImportError as e:
        return {"error": str(e)}

    body = {
        "snippet": {
            "title":       title[:100],
            "description": description[:5000],
            "tags":        tags[:15],
            "categoryId":  category_id,
        },
        "status": {
            "privacyStatus":          privacy,
            "selfDeclaredMadeForKids": False,
            "notifySubscribers":      notify_subscribers,
        },
    }
    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = service.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    try:
        while response is None:
            status, response = request.next_chunk()
    except Exception as e:
        return {"error": f"upload failed: {e}"}

    return {
        "status":   "uploaded",
        "video_id": response.get("id"),
        "url":      f"https://youtube.com/watch?v={response.get('id')}",
        "shorts_url": f"https://youtube.com/shorts/{response.get('id')}",
        "uploaded_at": datetime.now().isoformat(),
    }
