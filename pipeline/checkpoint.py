"""Stage artifact reuse helpers — avoid re-running expensive work when valid."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pipeline.audio import AUDIO_MODE_KEY, CONTINUOUS_NARRATION_KEY
from pipeline.models import EpisodeScript

logger = logging.getLogger(__name__)


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def load_valid_assets(
    episode_dir: Path,
    script: EpisodeScript,
    *,
    log: bool = True,
) -> dict[str, dict[str, str | None]] | None:
    """Return assets manifest if every shot has an existing image file."""
    data = _read_json(episode_dir / "assets.json")
    if not isinstance(data, dict) or not data:
        return None
    for shot in script.shots:
        row = data.get(shot.shot_id)
        if not isinstance(row, dict):
            return None
        image = row.get("image")
        if not image or not Path(str(image)).is_file():
            return None
        clip = row.get("clip")
        if clip and not Path(str(clip)).is_file():
            row = {**row, "clip": None}
            data[shot.shot_id] = row
    if log:
        logger.info("Checkpoint: reusing assets.json (%d shots)", len(script.shots))
    return data  # type: ignore[return-value]


def load_valid_audio(
    episode_dir: Path,
    script: EpisodeScript,
    *,
    log: bool = True,
) -> dict[str, str] | None:
    """Return audio manifest if expected narration / shot files exist."""
    data = _read_json(episode_dir / "audio.json")
    if not isinstance(data, dict) or not data:
        return None
    mode = data.get(AUDIO_MODE_KEY) or data.get("_mode")
    if mode == "continuous" or CONTINUOUS_NARRATION_KEY in data:
        narr = data.get(CONTINUOUS_NARRATION_KEY)
        if not narr or not Path(str(narr)).is_file():
            return None
    for shot in script.shots:
        path = data.get(shot.shot_id)
        if not path or not Path(str(path)).is_file():
            return None
    if log:
        logger.info("Checkpoint: reusing audio.json (%s)", mode or "per_shot")
    return {str(k): str(v) for k, v in data.items() if v is not None}


def load_valid_final(episode_dir: Path, *, log: bool = True) -> Path | None:
    final = episode_dir / "final.mp4"
    if final.is_file() and final.stat().st_size > 10_000:
        if log:
            logger.info("Checkpoint: reusing final.mp4")
        return final
    return None
