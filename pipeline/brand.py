"""Load brand bible assets for the active project."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

import config


def clear_caches() -> None:
    load_characters.cache_clear()


@lru_cache(maxsize=8)
def load_characters() -> dict[str, Any]:
    path = config.BRAND_DIR / "characters.json"
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    # Cache key includes project id via wrapper below — store project on result
    data["_project_id"] = config.PROJECT_ID
    return data


def _characters() -> dict[str, Any]:
    data = load_characters()
    if data.get("_project_id") != config.PROJECT_ID:
        clear_caches()
        data = load_characters()
    return data


def character_ids() -> list[str]:
    return list(_characters().get("characters", {}).keys())


def allowed_speakers() -> set[str]:
    return set(character_ids()) | {"narrator"}


def core_characters() -> list[str]:
    chars = _characters().get("characters", {})
    core = [cid for cid, c in chars.items() if c.get("tier") == "core"]
    return core or character_ids()


def character_prompt(character_id: str) -> str:
    chars = _characters().get("characters", {})
    if character_id not in chars:
        return ""
    return chars[character_id]["visual_prompt"]


def character_negative(character_id: str) -> str:
    chars = _characters().get("characters", {})
    if character_id not in chars:
        return "text, watermark, violence, horror"
    return chars[character_id]["negative_prompt"]


def style_suffix() -> str:
    style_path = config.BRAND_DIR / "style.md"
    if not style_path.is_file():
        return "cinematic still, no text, no watermark"
    text = style_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("STYLE_SUFFIX:"):
            return line.split(":", 1)[1].strip()
    return "cinematic still, no text, no watermark"


def safety_rules() -> str:
    path = config.BRAND_DIR / "safety.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def reference_path(character_id: str) -> Path | None:
    chars = _characters().get("characters", {})
    if character_id not in chars:
        return None
    rel = chars[character_id].get("reference_image") or ""
    if not rel:
        return None
    path = config.PROJECT_ROOT / rel
    return path if path.is_file() and path.stat().st_size > 0 else None


def reference_paths_for(character_ids: list[str]) -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for cid in character_ids:
        path = reference_path(cid)
        if path and path not in seen:
            paths.append(path)
            seen.add(path)
    return paths


def build_shot_prompt(visual_prompt: str, characters: list[str]) -> str:
    parts = [visual_prompt.strip()]
    for cid in characters:
        cp = character_prompt(cid)
        if cp:
            parts.append(cp)
    if characters:
        names = ", ".join(characters)
        display = config.PROJECT.get("display_name") or config.PROJECT_ID
        parts.append(
            f"keep identity locked to official {display} brand reference sheets for: {names}"
        )
    parts.append(style_suffix())
    return ", ".join(parts)


def tts_voice_for(speaker: str) -> dict[str, str]:
    data = _characters()
    chars = data.get("characters", {})
    if speaker in chars:
        c = chars[speaker]
        return {
            "openai": c.get("tts_voice", "alloy"),
            "elevenlabs": c.get("elevenlabs_voice_id", ""),
        }
    narrator = data.get("narrator") or {}
    return {
        "openai": narrator.get("tts_voice", "alloy"),
        "elevenlabs": narrator.get("elevenlabs_voice_id", ""),
    }


def brand_refs_exist() -> list[Path]:
    refs = []
    for c in _characters().get("characters", {}).values():
        rel = c.get("reference_image", "")
        if not rel:
            continue
        p = config.PROJECT_ROOT / rel
        if p.is_file():
            refs.append(p)
    return refs
