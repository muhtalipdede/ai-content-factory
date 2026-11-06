"""Episode history, pause flag, and soft cost accounting."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import config

_TOPIC_STOPWORDS = {
    "ama",
    "bir",
    "bu",
    "da",
    "de",
    "için",
    "ile",
    "kimse",
    "bilmiyor",
    "the",
    "and",
    "for",
    "with",
}


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def is_paused() -> bool:
    return config.PAUSE_FLAG.exists()


def set_pause(paused: bool = True) -> None:
    if paused:
        config.PAUSE_FLAG.write_text(
            f"paused_at={datetime.now(timezone.utc).isoformat()}\n",
            encoding="utf-8",
        )
    elif config.PAUSE_FLAG.exists():
        config.PAUSE_FLAG.unlink()


def load_history() -> list[dict[str, Any]]:
    data = _read_json(config.HISTORY_PATH, [])
    return data if isinstance(data, list) else []


def published_history(*, include_dry_run: bool = False) -> list[dict[str, Any]]:
    """History entries used for learning / dedupe (excludes dry-runs by default)."""
    rows = load_history()
    if include_dry_run:
        return rows
    return [item for item in rows if not item.get("dry_run")]


def normalize_topic_key(text: str) -> str:
    """Lowercase alphanumeric tokens for title/claim similarity."""
    raw = (text or "").lower()
    raw = raw.replace(":", " ").replace("—", " ").replace("-", " ")
    tokens = re.findall(r"[a-z0-9çğıöşü]+", raw, flags=re.IGNORECASE)
    kept = [t for t in tokens if len(t) > 2 and t not in _TOPIC_STOPWORDS]
    return " ".join(kept)


def token_jaccard(a: str, b: str) -> float:
    sa = set((a or "").split())
    sb = set((b or "").split())
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def find_similar_topic(
    *,
    title: str = "",
    claim: str = "",
    limit: int = 40,
    threshold: float = 0.55,
) -> dict[str, Any] | None:
    """Return a prior published history item if title/claim overlap is too high."""
    candidates = [
        normalize_topic_key(title),
        normalize_topic_key(claim),
    ]
    candidates = [c for c in candidates if c]
    if not candidates:
        return None

    for item in reversed(published_history()[-limit:]):
        priors = [
            str(item.get("normalized_title") or ""),
            str(item.get("normalized_claim") or ""),
            normalize_topic_key(str(item.get("title") or "")),
            normalize_topic_key(str(item.get("claim") or item.get("summary") or "")),
        ]
        priors = [p for p in priors if p]
        for cand in candidates:
            for prior in priors:
                if token_jaccard(cand, prior) >= threshold:
                    return item
    return None


def previous_summaries(limit: int = 20) -> list[str]:
    out = []
    for item in published_history()[-limit:]:
        summary = item.get("summary") or item.get("title")
        if summary:
            out.append(str(summary))
    return out


def append_history(entry: dict[str, Any]) -> None:
    if entry.get("dry_run"):
        return
    history = load_history()
    entry = {
        **entry,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    }
    history.append(entry)
    _write_json(config.HISTORY_PATH, history)


def load_cost() -> dict[str, Any]:
    data = _read_json(config.COST_PATH, {})
    return data if isinstance(data, dict) else {}


def _today_key() -> str:
    return date.today().isoformat()


def today_stats() -> dict[str, float | int]:
    cost = load_cost()
    day = cost.get(_today_key(), {})
    return {
        "episodes": int(day.get("episodes", 0)),
        "cost_usd": float(day.get("cost_usd", 0.0)),
    }


def record_cost(amount_usd: float, *, episode: bool = False) -> None:
    cost = load_cost()
    key = _today_key()
    day = cost.get(key, {"episodes": 0, "cost_usd": 0.0})
    day["cost_usd"] = float(day.get("cost_usd", 0.0)) + float(amount_usd)
    if episode:
        day["episodes"] = int(day.get("episodes", 0)) + 1
    cost[key] = day
    _write_json(config.COST_PATH, cost)


def can_produce() -> tuple[bool, str]:
    if is_paused():
        return False, f"Paused: {config.PAUSE_FLAG} exists. Remove it to resume."
    stats = today_stats()
    if stats["episodes"] >= config.DAILY_MAX_EPISODES:
        return (
            False,
            f"Daily episode cap reached ({stats['episodes']}/{config.DAILY_MAX_EPISODES}).",
        )
    if stats["cost_usd"] >= config.DAILY_MAX_COST_USD:
        return (
            False,
            f"Daily cost cap reached (${stats['cost_usd']:.2f}/${config.DAILY_MAX_COST_USD:.2f}).",
        )
    return True, "ok"


def estimate_episode_cost(num_shots: int, ai_video_shots: int = 0) -> float:
    return (
        config.EST_COST_SCRIPT
        + num_shots * config.EST_COST_IMAGE
        + num_shots * config.EST_COST_TTS_PER_SHOT
        + ai_video_shots * config.EST_COST_AI_VIDEO
    )


def within_budget(estimated: float) -> tuple[bool, str]:
    stats = today_stats()
    projected = float(stats["cost_usd"]) + estimated
    if projected > config.DAILY_MAX_COST_USD:
        return (
            False,
            f"Estimated ${estimated:.2f} would exceed daily cap "
            f"(${projected:.2f} > ${config.DAILY_MAX_COST_USD:.2f}).",
        )
    return True, "ok"
