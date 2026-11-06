"""Local script queue — write episodes here, pipeline consumes them (no LLM cost)."""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import config
from pipeline.brand import allowed_speakers, core_characters
from pipeline.models import EpisodeScript, Shot
from pipeline.safety import sanitize_dialogue, validate_script
from pipeline.script import _limit_ai_video_shots


ALLOWED_MOTION = {"still", "pan", "ai_video"}


def ensure_script_dirs() -> None:
    for path in (
        config.SCRIPTS_QUEUE_DIR,
        config.SCRIPTS_DONE_DIR,
        config.SCRIPTS_DIR / "templates",
    ):
        path.mkdir(parents=True, exist_ok=True)


def list_queued() -> list[Path]:
    ensure_script_dirs()
    return sorted(config.SCRIPTS_QUEUE_DIR.glob("*.json"))


def _default_description(script: EpisodeScript) -> str:
    parts = [script.summary]
    if script.lesson:
        parts.append(f"Ders: {script.lesson}")
    if script.sources:
        parts.append("Kaynaklar:\n" + "\n".join(f"- {s}" for s in script.sources))
    footer = config.DESCRIPTION_FOOTER or ""
    if footer:
        parts.append(footer)
    return "\n\n".join(p for p in parts if p)


def load_script_file(path: Path) -> EpisodeScript:
    raw = json.loads(path.read_text(encoding="utf-8"))
    speakers = allowed_speakers()
    shots_in = raw.get("shots") or []
    shots = []
    for i, s in enumerate(shots_in, start=1):
        speaker = str(s.get("speaker") or "narrator").strip().lower()
        if config.SPEAKERS_MODE == "narrator_only":
            speaker = "narrator"
        elif speaker not in speakers:
            speaker = "narrator"
        motion = str(s.get("motion") or "still").strip().lower()
        if motion not in ALLOWED_MOTION:
            motion = "still"
        chars = [
            c
            for c in (str(x).strip().lower() for x in (s.get("characters") or []))
            if c in speakers and c != "narrator"
        ]
        if config.SPEAKERS_MODE == "narrator_only":
            chars = []
        shots.append(
            Shot(
                shot_id=s.get("shot_id") or f"s{i:02d}",
                duration_sec=float(s.get("duration_sec") or 8),
                speaker=speaker,
                dialogue=sanitize_dialogue(s.get("dialogue") or ""),
                visual_prompt=(s.get("visual_prompt") or "").strip(),
                characters=chars,
                motion=motion,  # type: ignore[arg-type]
                sfx=s.get("sfx"),
            )
        )

    sources = [str(s).strip() for s in (raw.get("sources") or []) if str(s).strip()]
    tags = list(raw.get("tags") or config.DEFAULT_TAGS or [])
    script = EpisodeScript(
        episode_id=str(raw.get("episode_id") or path.stem),
        title=str(raw.get("title") or config.PROJECT.get("display_name") or "Episode").strip(),
        summary=str(raw.get("summary") or "").strip(),
        lesson=str(raw.get("lesson") or "").strip(),
        tags=tags,
        description=str(raw.get("description") or "").strip(),
        sources=sources,
        shots=shots,
    )
    if not script.description:
        script.description = _default_description(script)
    elif script.sources and "http" not in script.description.lower():
        script.description = (
            script.description.rstrip()
            + "\n\nKaynaklar:\n"
            + "\n".join(f"- {s}" for s in script.sources)
        )
    script.recompute_duration()
    script = _limit_ai_video_shots(script)
    ok, errors = validate_script(script)
    if not ok:
        raise ValueError(f"Script invalid ({path.name}): " + "; ".join(errors))
    return script


def next_queued_script() -> tuple[EpisodeScript, Path] | None:
    queued = list_queued()
    if not queued:
        return None
    path = queued[0]
    return load_script_file(path), path


def mark_done(source_path: Path, episode_id: str) -> Path:
    ensure_script_dirs()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    dest = config.SCRIPTS_DONE_DIR / f"{stamp}_{episode_id}.json"
    if source_path.exists():
        shutil.move(str(source_path), str(dest))
    return dest


def write_template_if_missing() -> Path:
    ensure_script_dirs()
    template_path = config.SCRIPTS_DIR / "templates" / "episode.template.json"
    if template_path.exists():
        return template_path
    chars = [] if config.SPEAKERS_MODE == "narrator_only" else core_characters()
    template = {
        "episode_id": "ep_ornek_001",
        "title": "Örnek Başlık",
        "summary": "Kısa özet.",
        "lesson": "",
        "tags": list(config.DEFAULT_TAGS or ["shorts"]),
        "description": "",
        "sources": [],
        "shots": [
            {
                "shot_id": "s01",
                "duration_sec": 8,
                "speaker": "narrator",
                "dialogue": "Açılış.",
                "visual_prompt": "cinematic still matching the topic",
                "characters": chars,
                "motion": "pan",
            }
        ],
    }
    template_path.write_text(
        json.dumps(template, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return template_path
