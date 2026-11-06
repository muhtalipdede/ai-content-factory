"""Hybrid AI video generation for selected shots.

MVP: stub returns None so compose falls back to still + Ken Burns.
When VIDEO_PROVIDER is runway|kling|fal and keys exist, generate a short clip.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

import config
from pipeline.models import Shot

logger = logging.getLogger(__name__)


def generate_clip(
    shot: Shot,
    image_path: Path,
    out_path: Path,
    *,
    dry_run: bool = False,
) -> Path | None:
    """Generate a short motion clip from a still. Returns path or None on stub/failure."""
    if shot.motion != "ai_video":
        return None
    if dry_run or config.VIDEO_PROVIDER == "stub":
        logger.info("hybrid_video stub for %s — compose will use Ken Burns", shot.shot_id)
        return None

    duration = min(float(shot.duration_sec), config.AI_VIDEO_MAX_SEC)
    prompt = f"Gentle children's cartoon camera motion: {shot.visual_prompt}"

    try:
        if config.VIDEO_PROVIDER == "fal":
            return _fal_image_to_video(image_path, out_path, prompt, duration)
        if config.VIDEO_PROVIDER == "runway":
            return _runway_image_to_video(image_path, out_path, prompt, duration)
        if config.VIDEO_PROVIDER == "kling":
            return _kling_image_to_video(image_path, out_path, prompt, duration)
        logger.warning("Unknown VIDEO_PROVIDER=%s", config.VIDEO_PROVIDER)
        return None
    except Exception as exc:  # noqa: BLE001 — soft-fail to still
        logger.exception("AI video failed for %s: %s", shot.shot_id, exc)
        return None


def _fal_image_to_video(
    image_path: Path, out_path: Path, prompt: str, duration: float
) -> Path | None:
    if not config.FAL_KEY:
        logger.warning("FAL_KEY missing; skipping AI video")
        return None
    # fal.ai Kling / MiniMax image-to-video style endpoint (best-effort)
    headers = {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}
    with image_path.open("rb") as f:
        image_bytes = f.read()
    import base64

    b64 = base64.b64encode(image_bytes).decode("ascii")
    payload = {
        "prompt": prompt,
        "image_url": f"data:image/png;base64,{b64}",
        "duration": str(int(max(2, min(duration, 5)))),
    }
    with httpx.Client(timeout=180.0) as client:
        # Queue-style API; for MVP use sync mini endpoint if available
        resp = client.post(
            "https://fal.run/fal-ai/minimax-video/image-to-video",
            headers=headers,
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("fal video error: %s %s", resp.status_code, resp.text[:500])
            return None
        data = resp.json()
        video_url = data.get("video", {}).get("url") or data.get("url")
        if not video_url:
            return None
        video = client.get(video_url)
        video.raise_for_status()
        out_path.write_bytes(video.content)
    return out_path


def _runway_image_to_video(
    image_path: Path, out_path: Path, prompt: str, duration: float
) -> Path | None:
    if not config.RUNWAY_API_KEY:
        logger.warning("RUNWAY_API_KEY missing; skipping AI video")
        return None
    # Runway Gen-3/4 image-to-video REST (simplified; may need SDK updates)
    headers = {
        "Authorization": f"Bearer {config.RUNWAY_API_KEY}",
        "Content-Type": "application/json",
        "X-Runway-Version": "2024-11-06",
    }
    import base64

    b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    payload = {
        "promptText": prompt,
        "promptImage": f"data:image/png;base64,{b64}",
        "model": "gen3a_turbo",
        "duration": int(max(5, min(duration, 5))),
        "ratio": "768:1280" if config.VIDEO_FORMAT == "shorts" else "1280:768",
    }
    with httpx.Client(timeout=300.0) as client:
        resp = client.post(
            "https://api.dev.runwayml.com/v1/image_to_video",
            headers=headers,
            json=payload,
        )
        if resp.status_code >= 400:
            logger.error("runway error: %s %s", resp.status_code, resp.text[:500])
            return None
        task = resp.json()
        task_id = task.get("id")
        if not task_id:
            return None
        # Poll briefly
        for _ in range(60):
            st = client.get(
                f"https://api.dev.runwayml.com/v1/tasks/{task_id}",
                headers=headers,
            )
            body = st.json()
            status = body.get("status")
            if status == "SUCCEEDED":
                url = (body.get("output") or [None])[0]
                if not url:
                    return None
                vid = client.get(url)
                out_path.write_bytes(vid.content)
                return out_path
            if status in {"FAILED", "CANCELLED"}:
                return None
            import time

            time.sleep(5)
    return None


def _kling_image_to_video(
    image_path: Path, out_path: Path, prompt: str, duration: float
) -> Path | None:
    if not config.KLING_API_KEY:
        logger.warning("KLING_API_KEY missing; skipping AI video")
        return None
    # Placeholder for Kling official API shape — soft-fail if unavailable
    logger.warning(
        "Kling provider configured but direct API wiring is best-effort; "
        "falling back to stub for %s",
        image_path.name,
    )
    _ = (prompt, duration, out_path)
    return None
