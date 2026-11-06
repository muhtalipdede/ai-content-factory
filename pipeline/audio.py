"""TTS per shot / continuous narration and bed music helpers."""

from __future__ import annotations

import json
import logging
import subprocess
import wave
from pathlib import Path

import config
from pipeline import state
from pipeline.brand import tts_voice_for
from pipeline.models import EpisodeScript, Shot

logger = logging.getLogger(__name__)

CONTINUOUS_NARRATION_KEY = "_narration"
AUDIO_MODE_KEY = "_mode"


def _silent_wav(out_path: Path, duration_sec: float, rate: int = 24000) -> Path:
    n_frames = int(rate * duration_sec)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out_path), "w") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(b"\x00\x00" * n_frames)
    return out_path


def _resolve_voice(default_voice: str) -> str:
    """Prefer project-locked voice so gender/timbre cannot drift per call."""
    project_voice = str(config.PROJECT.get("tts_voice") or "").strip()
    return project_voice or default_voice or "onyx"


def _openai_tts(text: str, voice: str, out_path: Path) -> Path:
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path = out_path.with_suffix(".mp3")
    locked_voice = _resolve_voice(voice)
    kwargs: dict = {
        "model": config.OPENAI_TTS_MODEL,
        "voice": locked_voice,
        "input": text,
    }
    if abs(config.TTS_SPEED - 1.0) > 0.01:
        kwargs["speed"] = config.TTS_SPEED
    instructions = (config.PROJECT.get("tts_instructions") or "").strip()
    if instructions:
        kwargs["instructions"] = instructions

    logger.info("TTS voice=%s model=%s chars=%d", locked_voice, config.OPENAI_TTS_MODEL, len(text))
    try:
        with client.audio.speech.with_streaming_response.create(**kwargs) as response:
            response.stream_to_file(mp3_path)
    except Exception:
        kwargs.pop("speed", None)
        try:
            with client.audio.speech.with_streaming_response.create(**kwargs) as response:
                response.stream_to_file(mp3_path)
        except Exception:
            # Last resort: drop instructions but KEEP locked voice
            kwargs.pop("instructions", None)
            kwargs["voice"] = locked_voice
            with client.audio.speech.with_streaming_response.create(**kwargs) as response:
                response.stream_to_file(mp3_path)
    return mp3_path


def _elevenlabs_tts(text: str, voice_id: str, out_path: Path) -> Path:
    import httpx

    if not config.ELEVENLABS_API_KEY or not voice_id:
        raise RuntimeError("ElevenLabs key/voice_id missing")
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": config.ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text,
        "model_id": "eleven_multilingual_v2",
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.75},
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    mp3_path = out_path.with_suffix(".mp3")
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        mp3_path.write_bytes(resp.content)
    return mp3_path


def synthesize_shot(
    shot: Shot,
    out_path: Path,
    *,
    dry_run: bool = False,
) -> Path:
    text = shot.dialogue.strip() or "..."
    voices = tts_voice_for(shot.speaker)

    if dry_run or (config.TTS_PROVIDER == "openai" and not config.OPENAI_API_KEY):
        return _silent_wav(out_path.with_suffix(".wav"), max(shot.duration_sec, 2.0))

    if config.TTS_PROVIDER == "elevenlabs":
        path = _elevenlabs_tts(text, voices["elevenlabs"], out_path)
        state.record_cost(config.EST_COST_TTS_PER_SHOT)
        return path

    path = _openai_tts(text, voices["openai"], out_path)
    state.record_cost(config.EST_COST_TTS_PER_SHOT)
    return path


def _probe_duration(path: Path) -> float:
    cmd = [
        config.FFPROBE_BIN,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    try:
        out = subprocess.check_output(cmd, text=True).strip()
        return float(out)
    except Exception:  # noqa: BLE001
        return 0.0


def _slice_audio(src: Path, start: float, duration: float, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Tiny fades keep slice edges from clicking if ever used alone
    fade_d = min(0.05, max(duration / 8, 0.02))
    af = (
        f"afade=t=in:st=0:d={fade_d:.3f},"
        f"afade=t=out:st={max(duration - fade_d, 0):.3f}:d={fade_d:.3f}"
    )
    cmd = [
        config.FFMPEG_BIN,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(src),
        "-af",
        af,
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


def _generate_continuous_narration(
    script: EpisodeScript,
    episode_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    """One fluid TTS pass, then character-weighted slices for shot timing."""
    audio_dir = episode_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    voices = tts_voice_for("narrator")
    parts = [s.dialogue.strip() for s in script.shots if s.dialogue.strip()]
    full_text = " ".join(parts)
    narration_path = audio_dir / "narration.mp3"

    if dry_run or (config.TTS_PROVIDER == "openai" and not config.OPENAI_API_KEY):
        approx = max(sum(max(s.duration_sec, 3.0) for s in script.shots), 8.0)
        _silent_wav(narration_path.with_suffix(".wav"), approx)
        narration_path = narration_path.with_suffix(".wav")
    elif config.TTS_PROVIDER == "elevenlabs":
        _elevenlabs_tts(full_text, voices["elevenlabs"], narration_path)
        state.record_cost(config.EST_COST_TTS_PER_SHOT * max(len(script.shots), 1) * 0.7)
    else:
        _openai_tts(full_text, voices["openai"], narration_path)
        state.record_cost(config.EST_COST_TTS_PER_SHOT * max(len(script.shots), 1) * 0.7)

    total = _probe_duration(narration_path)
    manifest: dict[str, str] = {
        CONTINUOUS_NARRATION_KEY: str(narration_path),
        AUDIO_MODE_KEY: "continuous",
    }

    # Align words to audio so shot cuts + captions follow real speech
    aligned_words: list = []
    if not dry_run and narration_path.suffix.lower() in {".mp3", ".wav", ".m4a"}:
        try:
            from pipeline.captions import align_narration_words, apply_alignment_to_shots

            aligned_words = align_narration_words(narration_path)
            if aligned_words:
                apply_alignment_to_shots(script, aligned_words)
                words_path = audio_dir / "narration.words.json"
                words_path.write_text(
                    json.dumps(aligned_words, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                manifest["_words"] = str(words_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Word alignment failed (%s); falling back to char weights", exc)

    if not aligned_words:
        weights = [max(len(s.dialogue.strip()), 1) for s in script.shots]
        weight_sum = sum(weights) or 1
        cursor = 0.0
        for i, shot in enumerate(script.shots):
            if i == len(script.shots) - 1:
                dur = max(total - cursor, 0.4)
            else:
                dur = max(total * (weights[i] / weight_sum), 0.4)
            shot.duration_sec = round(dur, 2)
            cursor += dur
        script.recompute_duration()

    # Timing reference slices (not used as final voice when continuous)
    cursor = 0.0
    for i, shot in enumerate(script.shots):
        dur = float(shot.duration_sec)
        slice_path = audio_dir / f"{shot.shot_id}.mp3"
        if dry_run and narration_path.suffix == ".wav":
            manifest[shot.shot_id] = str(narration_path)
        else:
            _slice_audio(narration_path, cursor, dur, slice_path)
            manifest[shot.shot_id] = str(slice_path)
        cursor += dur

    (episode_dir / "audio.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    logger.info(
        "Continuous narration: %.1fs across %d shots (aligned=%s)",
        total,
        len(script.shots),
        bool(aligned_words),
    )
    return manifest


def generate_audio(
    script: EpisodeScript,
    episode_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, str]:
    # Faceless / narrator-only: one continuous take feels much smoother
    if config.SPEAKERS_MODE == "narrator_only":
        return _generate_continuous_narration(script, episode_dir, dry_run=dry_run)

    audio_dir = episode_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, str] = {AUDIO_MODE_KEY: "per_shot"}
    for shot in script.shots:
        base = audio_dir / shot.shot_id
        path = synthesize_shot(shot, base, dry_run=dry_run)
        dur = _probe_duration(path)
        if dur > shot.duration_sec + 0.5:
            shot.duration_sec = round(dur + 0.15, 2)
        manifest[shot.shot_id] = str(path)
    script.recompute_duration()
    (episode_dir / "audio.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    return manifest


def find_bed_music() -> Path | None:
    if not config.MUSIC_DIR.exists():
        return None
    for ext in ("*.mp3", "*.wav", "*.m4a"):
        matches = sorted(config.MUSIC_DIR.glob(ext))
        if matches:
            return matches[0]
    return None
