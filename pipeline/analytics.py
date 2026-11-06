"""YouTube public statistics snapshots for learning memory.

Uses Data API videos.list statistics when authorized. Does not require
YouTube Analytics API; unavailable metrics are recorded gracefully.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from pipeline import memory

logger = logging.getLogger(__name__)


def _youtube_service():
    """Reuse upload credentials; may lack scopes for some channels."""
    from pipeline.publish import _youtube_service as build

    return build()


def fetch_video_statistics(video_ids: list[str]) -> dict[str, dict[str, Any]]:
    """Return map video_id -> {ok, metrics|error}."""
    cleaned = [v.strip() for v in video_ids if str(v).strip()]
    out: dict[str, dict[str, Any]] = {vid: {"ok": False, "error": "not fetched"} for vid in cleaned}
    if not cleaned:
        return out
    if not config.YOUTUBE_TOKEN_PATH.exists():
        for vid in cleaned:
            out[vid] = {"ok": False, "error": "missing youtube token"}
        return out

    try:
        youtube = _youtube_service()
        # Batch in chunks of 50
        for i in range(0, len(cleaned), 50):
            chunk = cleaned[i : i + 50]
            resp = (
                youtube.videos()
                .list(part="statistics,snippet,contentDetails", id=",".join(chunk))
                .execute()
            )
            found: set[str] = set()
            for item in resp.get("items") or []:
                vid = item.get("id")
                if not vid:
                    continue
                found.add(vid)
                stats = item.get("statistics") or {}
                snippet = item.get("snippet") or {}
                out[vid] = {
                    "ok": True,
                    "metrics": {
                        "viewCount": int(stats["viewCount"]) if "viewCount" in stats else None,
                        "likeCount": int(stats["likeCount"]) if "likeCount" in stats else None,
                        "commentCount": int(stats["commentCount"])
                        if "commentCount" in stats
                        else None,
                        "favoriteCount": int(stats["favoriteCount"])
                        if "favoriteCount" in stats
                        else None,
                        "title": snippet.get("title"),
                        "publishedAt": snippet.get("publishedAt"),
                        "duration": (item.get("contentDetails") or {}).get("duration"),
                    },
                    "source": "youtube.data.videos.list",
                }
            for vid in chunk:
                if vid not in found and out.get(vid, {}).get("error") == "not fetched":
                    out[vid] = {"ok": False, "error": "video not found or inaccessible"}
    except Exception as exc:  # noqa: BLE001
        logger.warning("YouTube statistics fetch failed: %s", exc)
        for vid in cleaned:
            if not out.get(vid, {}).get("ok"):
                out[vid] = {"ok": False, "error": str(exc.__class__.__name__)}
    return out


def snapshot_published_episodes(*, limit: int = 30) -> dict[str, Any]:
    """Pull stats for memory episodes that have youtube_video_id; store snapshots."""
    memory.sync_from_history()
    rows = [r for r in memory.list_episodes() if r.get("youtube_video_id")][:limit]
    ids = [str(r["youtube_video_id"]) for r in rows]
    stats_map = fetch_video_statistics(ids)
    results: list[dict[str, Any]] = []
    for row in rows:
        vid = str(row["youtube_video_id"])
        ep_id = str(row["episode_id"])
        payload = stats_map.get(vid) or {"ok": False, "error": "missing"}
        snap = {
            "youtube_video_id": vid,
            "ok": bool(payload.get("ok")),
            "error": payload.get("error"),
            "metrics": payload.get("metrics") or {},
            "source": payload.get("source") or "youtube.data.videos.list",
            "window": "latest",
        }
        memory.add_snapshot(ep_id, snap)
        results.append({"episode_id": ep_id, **snap})
    ok_n = sum(1 for r in results if r.get("ok"))
    return {
        "checked": len(results),
        "ok": ok_n,
        "failed": len(results) - ok_n,
        "results": results,
        "hints": memory.performance_hints(),
    }
