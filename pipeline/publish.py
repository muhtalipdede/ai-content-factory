"""YouTube Data API upload with OAuth refresh token."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import config
from pipeline.models import EpisodeMeta

logger = logging.getLogger(__name__)

# upload = publish; readonly = videos.list statistics for learning memory
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]
# Fixed port for browser flow (client_secret often lists only http://localhost).
OAUTH_LOCAL_PORT = 8080


def _scopes_satisfied(creds) -> bool:
    have = {str(s) for s in (getattr(creds, "scopes", None) or [])}
    need = set(SCOPES)
    return need.issubset(have)


def _authorization_code_from_user_input(raw: str) -> str:
    """Accept either a raw code or a full redirect URL containing ?code=."""
    text = (raw or "").strip().strip('"').strip("'")
    if not text:
        raise RuntimeError("Empty authorization input")
    if "code=" in text:
        from urllib.parse import parse_qs, urlparse

        query = parse_qs(urlparse(text).query)
        code = (query.get("code") or [""])[0].strip()
        if not code:
            raise RuntimeError("Redirect URL has no code= parameter")
        return code
    return text


def _run_console_oauth(flow):
    """Manual paste flow — google-auth-oauthlib no longer ships run_console()."""
    flow.redirect_uri = f"http://localhost:{OAUTH_LOCAL_PORT}/"
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        include_granted_scopes="true",
    )
    print("\n1) Open this URL in your browser:\n")
    print(auth_url)
    print(
        "\n2) Approve access. If the page says something went wrong, "
        "copy the FULL address-bar URL (it still contains code=...) "
        "and paste it below. You can also paste only the code value.\n"
    )
    raw = input("Redirect URL or code: ")
    code = _authorization_code_from_user_input(raw)
    flow.fetch_token(code=code)
    return flow.credentials


def _run_oauth_flow(*, console: bool = False):
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not config.YOUTUBE_CLIENT_SECRETS.exists():
        raise FileNotFoundError(
            f"Missing {config.YOUTUBE_CLIENT_SECRETS}. "
            "Download OAuth client JSON from Google Cloud Console."
        )
    flow = InstalledAppFlow.from_client_secrets_file(
        str(config.YOUTUBE_CLIENT_SECRETS), SCOPES
    )
    if console:
        logger.info("Console OAuth: open URL, approve, paste redirect URL or code.")
        return _run_console_oauth(flow)
    try:
        return flow.run_local_server(
            port=OAUTH_LOCAL_PORT,
            prompt="consent",
            access_type="offline",
            open_browser=True,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Localhost OAuth failed (%s). Falling back to console paste.",
            exc,
        )
        return _run_console_oauth(flow)


def _youtube_service(*, force_reauth: bool = False, console: bool = False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build

    config.CREDENTIALS_DIR.mkdir(parents=True, exist_ok=True)
    config.YOUTUBE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    if force_reauth and config.YOUTUBE_TOKEN_PATH.exists():
        config.YOUTUBE_TOKEN_PATH.unlink()

    creds = None
    if config.YOUTUBE_TOKEN_PATH.exists():
        creds = Credentials.from_authorized_user_file(
            str(config.YOUTUBE_TOKEN_PATH), SCOPES
        )

    needs_login = creds is None or not creds.valid or not _scopes_satisfied(creds)

    if needs_login:
        if (
            creds
            and creds.expired
            and creds.refresh_token
            and _scopes_satisfied(creds)
        ):
            creds.refresh(Request())
        else:
            if creds and not _scopes_satisfied(creds):
                logger.info(
                    "Token missing required scopes %s — starting consent again",
                    sorted(set(SCOPES) - set(creds.scopes or [])),
                )
            creds = _run_oauth_flow(console=console)
        config.YOUTUBE_TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    return build("youtube", "v3", credentials=creds)


def authorize_youtube(*, force: bool = False, console: bool = False) -> None:
    """Interactive OAuth; saves refresh token for uploads + analytics."""
    _youtube_service(force_reauth=force, console=console)
    logger.info("YouTube credentials saved to %s", config.YOUTUBE_TOKEN_PATH)


def publish_episode(
    meta: EpisodeMeta,
    episode_dir: Path,
    *,
    dry_run: bool = False,
) -> EpisodeMeta:
    if dry_run or config.DRY_RUN or not config.AUTO_PUBLISH:
        result = {
            "status": "skipped",
            "reason": "dry_run" if (dry_run or config.DRY_RUN) else "AUTO_PUBLISH=false",
            "meta": json.loads(meta.model_dump_json()),
        }
        (episode_dir / "publish.json").write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Publish skipped: %s", result["reason"])
        return meta

    existing_path = episode_dir / "publish.json"
    if existing_path.is_file():
        try:
            existing = json.loads(existing_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            existing = {}
        prior_id = str(existing.get("youtube_video_id") or "").strip()
        if existing.get("status") == "uploaded" and prior_id:
            meta.youtube_video_id = prior_id
            logger.info(
                "Publish idempotent skip: already uploaded youtube_video_id=%s",
                prior_id,
            )
            return meta

    if not meta.video_path or not Path(meta.video_path).is_file():
        raise FileNotFoundError("Video file missing for upload")

    from googleapiclient.http import MediaFileUpload

    youtube = _youtube_service()
    payload_path = episode_dir / "youtube_payload.json"
    body = json.loads(payload_path.read_text(encoding="utf-8"))

    media = MediaFileUpload(
        meta.video_path,
        mimetype="video/mp4",
        resumable=True,
        chunksize=1024 * 1024,
    )
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=media,
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            logger.info("Upload progress: %.1f%%", status.progress() * 100)

    video_id = response["id"]
    meta.youtube_video_id = video_id
    logger.info("Uploaded video id=%s", video_id)

    if meta.thumbnail_path and Path(meta.thumbnail_path).is_file():
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(meta.thumbnail_path, mimetype="image/jpeg"),
            ).execute()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Thumbnail upload failed (may need channel verification): %s", exc)

    (episode_dir / "metadata.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")
    (episode_dir / "publish.json").write_text(
        json.dumps(
            {
                "status": "uploaded",
                "youtube_video_id": video_id,
                "url": f"https://youtu.be/{video_id}",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return meta
