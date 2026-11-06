"""Persistent learning memory for published episodes and performance snapshots."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config
from pipeline import state


def memory_path() -> Path:
    return config.STATE_DIR / "learning_memory.json"


def _read() -> dict[str, Any]:
    path = memory_path()
    if not path.exists():
        return {"episodes": {}, "updated_at": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"episodes": {}, "updated_at": None}
    if not isinstance(data, dict):
        return {"episodes": {}, "updated_at": None}
    data.setdefault("episodes", {})
    return data


def _write(data: dict[str, Any]) -> None:
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def upsert_episode(entry: dict[str, Any]) -> dict[str, Any]:
    """Create/update a learning-memory episode record from a history-like entry."""
    data = _read()
    episode_id = str(entry.get("episode_id") or "").strip()
    if not episode_id:
        return {}
    existing = data["episodes"].get(episode_id) or {}
    record = {
        **existing,
        "episode_id": episode_id,
        "project_id": entry.get("project_id") or config.PROJECT_ID,
        "title": entry.get("title") or existing.get("title") or "",
        "claim": entry.get("claim") or existing.get("claim") or "",
        "normalized_title": entry.get("normalized_title")
        or state.normalize_topic_key(str(entry.get("title") or "")),
        "normalized_claim": entry.get("normalized_claim")
        or state.normalize_topic_key(str(entry.get("claim") or "")),
        "summary": entry.get("summary") or existing.get("summary") or "",
        "youtube_video_id": entry.get("youtube_video_id")
        or existing.get("youtube_video_id"),
        "path": entry.get("path") or existing.get("path"),
        "snapshots": existing.get("snapshots") or [],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not existing.get("created_at"):
        record["created_at"] = datetime.now(timezone.utc).isoformat()
    else:
        record["created_at"] = existing["created_at"]
    data["episodes"][episode_id] = record
    _write(data)
    return record


def add_snapshot(episode_id: str, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    data = _read()
    record = data["episodes"].get(episode_id)
    if not record:
        return None
    snaps = list(record.get("snapshots") or [])
    snap = {
        **snapshot,
        "captured_at": datetime.now(timezone.utc).isoformat(),
    }
    snaps.append(snap)
    # Keep last 12 snapshots per episode
    record["snapshots"] = snaps[-12:]
    # Convenience rollup from latest successful metrics
    metrics = snap.get("metrics") or {}
    if metrics:
        record["latest_metrics"] = metrics
        views = metrics.get("viewCount")
        if views is not None:
            try:
                record["latest_views"] = int(views)
            except (TypeError, ValueError):
                pass
    record["updated_at"] = snap["captured_at"]
    data["episodes"][episode_id] = record
    _write(data)
    return record


def list_episodes() -> list[dict[str, Any]]:
    data = _read()
    rows = list((data.get("episodes") or {}).values())
    rows.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return rows


def top_performing(*, limit: int = 5) -> list[dict[str, Any]]:
    rows = [r for r in list_episodes() if r.get("latest_views") is not None]
    rows.sort(key=lambda r: int(r.get("latest_views") or 0), reverse=True)
    return rows[:limit]


def performance_hints(*, limit: int = 5) -> dict[str, Any]:
    """Compact hints for ideation — correlation only, not causation."""
    top = top_performing(limit=limit)
    return {
        "note": "Provisional signals from latest view counts; not causal proof.",
        "top_titles": [r.get("title") for r in top if r.get("title")],
        "top_claims": [r.get("claim") for r in top if r.get("claim")],
        "sample_size": len(top),
    }


def sync_from_history() -> int:
    """Backfill memory entries from published history (no dry-runs)."""
    count = 0
    for item in state.published_history():
        if not item.get("episode_id"):
            continue
        upsert_episode(item)
        count += 1
    return count
