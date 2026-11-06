"""Assemble shot images/clips + audio into final.mp4 via FFmpeg."""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path

import config
from pipeline.audio import (
    AUDIO_MODE_KEY,
    CONTINUOUS_NARRATION_KEY,
    _probe_duration,
    find_bed_music,
)
from pipeline.captions import (
    ass_filter_arg,
    prepare_shot_captions,
    write_episode_ass_from_words,
)
from pipeline.models import EpisodeScript, Shot
from pipeline.motion import ken_burns_filter, select_motion_profile

logger = logging.getLogger(__name__)


def _run(cmd: list[str]) -> None:
    logger.debug("ffmpeg: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        err = (result.stderr or b"").decode("utf-8", errors="replace")[-2500:]
        raise RuntimeError(f"ffmpeg failed ({result.returncode}): {err}")


def _ken_burns_filter(
    shot: Shot,
    width: int,
    height: int,
    duration: float,
    *,
    shot_index: int = 0,
) -> str:
    return ken_burns_filter(shot, width, height, duration, shot_index=shot_index)


def _burn_caption_overlays(
    video_path: Path,
    overlays: list[tuple[Path, float, float]],
    out_path: Path,
) -> Path:
    if not overlays:
        return video_path

    cmd: list[str] = [config.FFMPEG_BIN, "-y", "-i", str(video_path)]
    for png, _, _ in overlays:
        cmd.extend(["-i", str(png)])

    parts: list[str] = []
    last = "[0:v]"
    for i, (_, start, end) in enumerate(overlays):
        inp = i + 1
        nxt = f"[v{i}]"
        parts.append(
            f"[{inp}:v]format=rgba[c{i}];"
            f"{last}[c{i}]overlay=0:0:enable='between(t,{start:.3f},{end:.3f})'{nxt}"
        )
        last = nxt

    filter_complex = ";".join(parts)
    cmd.extend(
        [
            "-filter_complex",
            filter_complex,
            "-map",
            last,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "copy",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    _run(cmd)
    return out_path


def _burn_ass(video_path: Path, ass_path: Path, out_path: Path) -> Path:
    vf = ass_filter_arg(ass_path)
    cmd = [
        config.FFMPEG_BIN,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        vf,
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _burn_episode_captions_synced(
    video_path: Path,
    words_path: Path,
    captions_dir: Path,
    out_path: Path,
    *,
    width: int,
    height: int,
) -> Path:
    """Burn full-episode karaoke ASS timed from Whisper word alignment."""
    import json

    words = json.loads(words_path.read_text(encoding="utf-8"))
    if not words:
        return video_path
    ass_path = captions_dir / "episode.ass"
    write_episode_ass_from_words(words, ass_path, width=width, height=height)
    if not ffmpeg_has_ass_safe():
        logger.warning("No libass; skipping synced episode captions")
        return video_path
    _burn_ass(video_path, ass_path, out_path)
    return out_path


def ffmpeg_has_ass_safe() -> bool:
    from pipeline.captions import ffmpeg_has_ass

    return ffmpeg_has_ass()


def _render_shot_video(
    shot: Shot,
    image_path: Path,
    audio_path: Path,
    clip_path: Path | None,
    out_path: Path,
    width: int,
    height: int,
    *,
    captions_dir: Path | None = None,
    shot_index: int = 0,
) -> Path:
    audio_dur = _probe_duration(audio_path)
    # Prefer script/audio timing without extra dead air (felt "choppy" before)
    duration = max(shot.duration_sec, audio_dur + 0.04, 1.0)
    shot.duration_sec = round(duration, 2)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    base_path = out_path.with_name(out_path.stem + "_base.mp4")

    if clip_path and clip_path.is_file():
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},fps=30,format=yuv420p"
        )
        cmd = [
            config.FFMPEG_BIN,
            "-y",
            "-stream_loop",
            "-1",
            "-i",
            str(clip_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(base_path),
        ]
    else:
        profile = select_motion_profile(shot, shot_index)
        logger.info("Shot %s motion profile=%s", shot.shot_id, profile)
        vf = _ken_burns_filter(
            shot, width, height, duration, shot_index=shot_index
        )
        cmd = [
            config.FFMPEG_BIN,
            "-y",
            "-loop",
            "1",
            "-i",
            str(image_path),
            "-i",
            str(audio_path),
            "-t",
            f"{duration:.3f}",
            "-vf",
            vf,
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            "-movflags",
            "+faststart",
            str(base_path),
        ]
    _run(cmd)

    if config.BURN_CAPTIONS and captions_dir is not None:
        prepared = prepare_shot_captions(
            shot,
            duration,
            captions_dir,
            width=width,
            height=height,
            accent=(shot_index == 0),
        )
        try:
            if prepared["mode"] == "ass" and prepared["ass"]:
                _burn_ass(base_path, prepared["ass"], out_path)
                base_path.unlink(missing_ok=True)
                return out_path
            if prepared["overlays"]:
                _burn_caption_overlays(base_path, prepared["overlays"], out_path)
                base_path.unlink(missing_ok=True)
                return out_path
        except Exception as exc:  # noqa: BLE001
            logger.warning("Caption burn failed (%s); using base segment", exc)

    base_path.replace(out_path)
    return out_path


def _concat_segments_hard(segments: list[Path], out_path: Path) -> Path:
    list_file = out_path.parent / "concat.txt"
    lines = [f"file '{seg.resolve()}'" for seg in segments]
    list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    cmd = [
        config.FFMPEG_BIN,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-c",
        "copy",
        str(out_path),
    ]
    try:
        _run(cmd)
    except Exception:
        cmd = [
            config.FFMPEG_BIN,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_file),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
        _run(cmd)
    return out_path


def _concat_segments_xfade(
    segments: list[Path],
    out_path: Path,
    *,
    fade: float = 0.32,
    video_only: bool = False,
) -> Path:
    """Soft-cut segments with xfade (+ acrossfade unless video_only)."""
    if len(segments) == 1:
        out_path.write_bytes(segments[0].read_bytes())
        return out_path

    durs = [_probe_duration(s) for s in segments]
    fade = min(fade, min(durs) / 2 - 0.05) if durs else fade
    fade = max(fade, 0.12)

    cmd: list[str] = [config.FFMPEG_BIN, "-y"]
    for seg in segments:
        cmd.extend(["-i", str(seg)])

    v_parts: list[str] = []
    a_parts: list[str] = []
    offset = durs[0] - fade
    v_last = "[0:v]"
    a_last = "[0:a]"
    for i in range(1, len(segments)):
        v_out = f"[vx{i}]"
        v_parts.append(
            f"{v_last}[{i}:v]xfade=transition=fade:duration={fade:.3f}:offset={offset:.3f}{v_out}"
        )
        v_last = v_out
        if not video_only:
            a_out = f"[ax{i}]"
            a_parts.append(f"{a_last}[{i}:a]acrossfade=d={fade:.3f}{a_out}")
            a_last = a_out
        offset += durs[i] - fade

    filter_complex = ";".join(v_parts + a_parts)
    cmd.extend(["-filter_complex", filter_complex, "-map", v_last])
    if video_only:
        cmd.extend(["-an"])
    else:
        cmd.extend(["-map", a_last, "-c:a", "aac"])
    cmd.extend(
        [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(out_path),
        ]
    )
    try:
        _run(cmd)
        return out_path
    except Exception as exc:
        logger.warning("xfade concat failed (%s); hard concat fallback", exc)
        return _concat_segments_hard(segments, out_path)


def _mux_narration(video_path: Path, narration_path: Path, out_path: Path) -> Path:
    """
    Lay continuous narration under video.

    Crossfaded video is often shorter than full narration (xfade overlap).
    Never use -shortest here — pad/freeze the last frame so the final sentence
    is never cut off mid-word.
    """
    v_dur = _probe_duration(video_path)
    a_dur = _probe_duration(narration_path)
    tail = 0.45  # breath after last word
    target = a_dur + tail
    pad_v = max(target - v_dur, 0.0)
    logger.info(
        "Mux narration: video=%.2fs audio=%.2fs pad_video=%.2fs target=%.2fs",
        v_dur,
        a_dur,
        pad_v,
        target,
    )
    filter_complex = (
        f"[0:v]tpad=stop_mode=clone:stop_duration={pad_v:.3f},format=yuv420p[v];"
        f"[1:a]apad=pad_dur={tail:.3f}[a]"
    )
    cmd = [
        config.FFMPEG_BIN,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(narration_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "[v]",
        "-map",
        "[a]",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-t",
        f"{target:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    _run(cmd)
    return out_path


def _concat_segments(segments: list[Path], out_path: Path, *, video_only: bool = False) -> Path:
    fade = float(config.PROJECT.get("segment_crossfade_sec") or 0.32)
    return _concat_segments_xfade(
        segments, out_path, fade=fade, video_only=video_only
    )


def _bed_music_volume() -> float:
    raw = config.PROJECT.get("bed_music_volume")
    try:
        vol = float(raw if raw is not None else 0.07)
    except (TypeError, ValueError):
        vol = 0.07
    return max(0.0, min(1.0, vol))


def _mix_bed_music(video_path: Path, music_path: Path, out_path: Path) -> Path:
    # Bed under voice: loudnorm voice first so ambient isn't crushed; duration follows voice.
    dur = _probe_duration(video_path)
    vol = _bed_music_volume()
    filter_complex = (
        f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[voice];"
        f"[1:a]volume={vol:.3f},aloop=loop=-1:size=2e+09[bg];"
        f"[voice][bg]amix=inputs=2:duration=first:dropout_transition=2:normalize=0[a]"
    )
    cmd = [
        config.FFMPEG_BIN,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(music_path),
        "-filter_complex",
        filter_complex,
        "-map",
        "0:v",
        "-map",
        "[a]",
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-t",
        f"{dur:.3f}",
        "-movflags",
        "+faststart",
        str(out_path),
    ]
    try:
        _run(cmd)
        return out_path
    except Exception:
        logger.warning("Bed music mix failed; keeping voice-only video")
        return video_path


def compose_episode(
    script: EpisodeScript,
    episode_dir: Path,
    assets: dict[str, dict[str, str | None]],
    audio: dict[str, str],
) -> Path:
    width, height = config.frame_size()
    segments_dir = episode_dir / "segments"
    captions_dir = episode_dir / "captions"
    segments_dir.mkdir(parents=True, exist_ok=True)
    if config.BURN_CAPTIONS:
        captions_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Path] = []
    continuous = audio.get(CONTINUOUS_NARRATION_KEY)
    audio_mode = audio.get(AUDIO_MODE_KEY) or "per_shot"
    use_continuous = bool(continuous) and audio_mode == "continuous"
    # Continuous mode: captions are burned once on the full timeline (Whisper-synced)
    per_shot_captions = config.BURN_CAPTIONS and not use_continuous

    for i, shot in enumerate(script.shots):
        asset = assets[shot.shot_id]
        image_path = Path(asset["image"])  # type: ignore[arg-type]
        clip_raw = asset.get("clip")
        clip_path = Path(clip_raw) if clip_raw else None
        audio_path = Path(audio[shot.shot_id])
        seg = segments_dir / f"{shot.shot_id}.mp4"
        _render_shot_video(
            shot,
            image_path,
            audio_path,
            clip_path,
            seg,
            width,
            height,
            captions_dir=captions_dir if per_shot_captions else None,
            shot_index=i,
        )
        segments.append(seg)

    script.recompute_duration()
    rough = episode_dir / "rough.mp4"

    # Continuous: hard-cut video (Whisper-timed shots) so captions stay on the
    # same absolute timeline as narration. Soft xfade would desync B-roll vs speech.
    if use_continuous:
        _concat_segments_hard(segments, rough)
    else:
        _concat_segments(segments, rough, video_only=False)

    voiced = episode_dir / "voiced.mp4"
    if use_continuous and continuous:
        logger.info("Muxing continuous narration for fluid voice")
        _mux_narration(rough, Path(continuous), voiced)
        source_for_mix = voiced
        words_path = audio.get("_words")
        if config.BURN_CAPTIONS and words_path and Path(words_path).is_file():
            captioned = episode_dir / "captioned.mp4"
            logger.info("Burning Whisper-synced episode captions")
            try:
                source_for_mix = _burn_episode_captions_synced(
                    voiced,
                    Path(words_path),
                    captions_dir,
                    captioned,
                    width=width,
                    height=height,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Synced episode captions failed (%s)", exc)
                source_for_mix = voiced
    else:
        source_for_mix = rough

    final = episode_dir / "final.mp4"
    music = find_bed_music()
    if music:
        logger.info("Mixing bed music: %s", music.name)
        mixed = _mix_bed_music(source_for_mix, music, final)
        if mixed.resolve() != final.resolve() and mixed.exists():
            if mixed.resolve() == source_for_mix.resolve():
                source_for_mix.replace(final)
            else:
                final.write_bytes(mixed.read_bytes())
    else:
        cmd = [
            config.FFMPEG_BIN,
            "-y",
            "-i",
            str(source_for_mix),
            "-af",
            "loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(final),
        ]
        try:
            _run(cmd)
        except Exception:
            source_for_mix.replace(final)

    meta = {
        "video": str(final),
        "width": width,
        "height": height,
        "duration_sec": script.total_duration_sec,
        "burn_captions": config.BURN_CAPTIONS,
        "audio_mode": audio_mode,
        "ffmpeg": config.FFMPEG_BIN,
        "bed_music": str(music) if music else None,
        "segments": [str(s) for s in segments],
    }
    (episode_dir / "compose.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return final
