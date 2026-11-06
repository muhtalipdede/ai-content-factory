"""Shorts captions: ASS karaoke (libass) with PNG overlay fallback."""

from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

import config
from pipeline.models import EpisodeScript, Shot

logger = logging.getLogger(__name__)


def chunk_dialogue(text: str, max_words: int = 4) -> list[str]:
    """Split dialogue into punchy on-screen phrases."""
    cleaned = re.sub(r"\s+", " ", (text or "").strip())
    if not cleaned:
        return []
    parts = re.split(r"(?<=[,:;.…!?])\s+", cleaned)
    chunks: list[str] = []
    for part in parts:
        words = part.split()
        if not words:
            continue
        if len(words) <= max_words:
            chunks.append(" ".join(words))
            continue
        for i in range(0, len(words), max_words):
            chunks.append(" ".join(words[i : i + max_words]))
    return chunks or [cleaned]


def ffmpeg_has_ass() -> bool:
    """True if active ffmpeg binary includes the ass filter (libass)."""
    try:
        out = subprocess.check_output(
            [config.FFMPEG_BIN, "-hide_banner", "-filters"],
            stderr=subprocess.STDOUT,
            text=True,
        )
    except Exception:  # noqa: BLE001
        return False
    # Match the dedicated filter line, not unrelated names like allpass/bass
    return bool(re.search(r"(?m)^\s*\S+\s+ass\s+", out))


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h}:{m:02d}:{s:05.2f}"


def _escape_ass_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace("{", "(")
        .replace("}", ")")
        .replace("\n", " ")
    )


def write_shot_ass_karaoke(
    shot: Shot,
    duration: float,
    out_path: Path,
    *,
    width: int,
    height: int,
    accent: bool = False,
) -> Path:
    """
    Word-by-word karaoke ASS.
    Soft white base + amber/teal highlight via \\k centisecond tags.
    """
    words = re.findall(r"\S+", (shot.dialogue or "").strip())
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not words:
        out_path.write_text("", encoding="utf-8")
        return out_path

    # Center-lower: above Shorts UI; keep side margins wide for Turkish words.
    margin_v = int(height * 0.22)
    margin_x = 110
    font_size = 68 if accent else 64
    # ASS colours are &HAABBGGRR
    primary = "&H00F0F0F0"  # resting words
    secondary = "&H0088D7FF" if accent else "&H00B4F0FF"  # karaoke fill (amber-ish)
    outline = "&H00000000"
    back = "&H96000000"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial Black,{font_size},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,0,0,1,5,2,2,{margin_x},{margin_x},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    usable = max(duration - 0.18, 0.5)
    start = 0.08
    end = start + usable
    # Distribute karaoke timing by character weight (longer words linger)
    weights = [max(len(re.sub(r"\W+", "", w)), 1) for w in words]
    total_w = sum(weights) or 1
    # \\k uses centiseconds
    total_cs = max(int(usable * 100), len(words) * 12)
    # Pop + word karaoke; soft line break every 3 words (Turkish words are long)
    rebuilt: list[str] = [r"{\fad(70,90)\fscx105\fscy105\t(0,120,\fscx100\fscy100)}"]
    assigned = 0
    for i, (word, weight) in enumerate(zip(words, weights)):
        if i == len(words) - 1:
            cs = max(total_cs - assigned, 8)
        else:
            cs = max(int(total_cs * (weight / total_w)), 8)
            assigned += cs
        rebuilt.append(rf"{{\k{cs}}}{_escape_ass_text(word)}")
        if i < len(words) - 1:
            rebuilt.append(r"\N" if (i + 1) % 3 == 0 else " ")
    body = "".join(rebuilt)

    lines = [
        header,
        f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Karaoke,,0,0,0,,{body}\n",
    ]
    out_path.write_text("".join(lines), encoding="utf-8")
    return out_path


def ass_filter_arg(ass_path: Path) -> str:
    """Escape path for ffmpeg ass= / subtitles= filter (list argv, no shell)."""
    # Prefer a short relative-friendly copy path without spaces when possible
    raw = str(ass_path.resolve())
    # Escape characters special to filtergraph
    escaped = (
        raw.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("[", "\\[")
        .replace("]", "\\]")
        .replace(",", "\\,")
        .replace(";", "\\;")
    )
    return f"ass={escaped}"


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Black.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap_line(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_w: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = " ".join(cur + [w])
        bbox = draw.textbbox((0, 0), trial, font=font)
        if bbox[2] - bbox[0] <= max_w or not cur:
            cur.append(w)
        else:
            lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def render_caption_png(
    text: str,
    out_path: Path,
    *,
    width: int,
    height: int,
    accent: bool = False,
) -> Path:
    """PNG fallback caption (used when libass unavailable)."""
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = _font(68 if accent else 62)
    max_w = int(width * 0.78)
    lines = _wrap_line(draw, text, font, max_w)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not lines:
        img.save(out_path, format="PNG")
        return out_path

    line_gap = 14
    sizes = [draw.textbbox((0, 0), ln, font=font) for ln in lines]
    heights = [b[3] - b[1] for b in sizes]
    block_h = sum(heights) + line_gap * (len(lines) - 1)
    y = int(height * 0.70) - block_h // 2
    fill = (255, 214, 120, 255) if accent else (250, 250, 250, 255)
    stroke = (0, 0, 0, 235)
    pad_x, pad_y = 32, 20
    bar_w = max(b[2] - b[0] for b in sizes) + pad_x * 2
    bar_h = block_h + pad_y * 2
    bar_x = (width - bar_w) // 2
    bar_y = y - pad_y
    draw.rounded_rectangle(
        (bar_x, bar_y, bar_x + bar_w, bar_y + bar_h),
        radius=20,
        fill=(0, 0, 0, 150),
    )
    cy = y
    for ln, h in zip(lines, heights):
        bbox = draw.textbbox((0, 0), ln, font=font)
        tw = bbox[2] - bbox[0]
        x = (width - tw) // 2
        draw.text((x, cy), ln, font=font, fill=fill, stroke_width=6, stroke_fill=stroke)
        cy += h + line_gap
    img.save(out_path, format="PNG")
    return out_path


def build_shot_caption_overlays(
    shot: Shot,
    duration: float,
    captions_dir: Path,
    *,
    width: int,
    height: int,
    accent: bool = False,
) -> list[tuple[Path, float, float]]:
    """PNG overlay timeline fallback (phrase chunks)."""
    chunks = chunk_dialogue(shot.dialogue, max_words=3)
    if not chunks:
        return []
    usable = max(duration - 0.12, 0.4)
    slot = usable / len(chunks)
    overlays: list[tuple[Path, float, float]] = []
    for i, phrase in enumerate(chunks):
        start = 0.06 + i * slot
        end = min(duration - 0.02, start + slot - 0.05)
        if end <= start:
            end = start + 0.25
        path = captions_dir / f"{shot.shot_id}_c{i:02d}.png"
        render_caption_png(phrase, path, width=width, height=height, accent=accent and i == 0)
        overlays.append((path, start, end))
    return overlays


def prepare_shot_captions(
    shot: Shot,
    duration: float,
    captions_dir: Path,
    *,
    width: int,
    height: int,
    accent: bool = False,
) -> dict:
    """
    Prefer ASS karaoke when ffmpeg has libass; else PNG overlays.
    Returns {"mode": "ass"|"png", "ass": Path|None, "overlays": [...]}.
    """
    captions_dir.mkdir(parents=True, exist_ok=True)
    if ffmpeg_has_ass():
        ass_path = captions_dir / f"{shot.shot_id}.ass"
        write_shot_ass_karaoke(
            shot, duration, ass_path, width=width, height=height, accent=accent
        )
        return {"mode": "ass", "ass": ass_path, "overlays": []}
    overlays = build_shot_caption_overlays(
        shot, duration, captions_dir, width=width, height=height, accent=accent
    )
    return {"mode": "png", "ass": None, "overlays": overlays}


def align_narration_words(narration_path: Path) -> list[dict[str, Any]]:
    """
    Force-align continuous narration via Whisper word timestamps.
    Returns [{word, start, end}, ...] in seconds.
    """
    if not config.OPENAI_API_KEY:
        return []
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    with narration_path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
            language="tr",
            response_format="verbose_json",
            timestamp_granularities=["word"],
        )
    words_raw = getattr(result, "words", None) or []
    aligned: list[dict[str, Any]] = []
    for w in words_raw:
        if isinstance(w, dict):
            token = str(w.get("word") or "").strip()
            start = float(w.get("start") or 0.0)
            end = float(w.get("end") or start)
        else:
            token = str(getattr(w, "word", "") or "").strip()
            start = float(getattr(w, "start", 0.0) or 0.0)
            end = float(getattr(w, "end", start) or start)
        if not token:
            continue
        if end <= start:
            end = start + 0.12
        aligned.append({"word": token, "start": start, "end": end})
    logger.info("Whisper aligned %d words from %s", len(aligned), narration_path.name)
    return aligned


def apply_alignment_to_shots(
    script: EpisodeScript,
    words: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Re-time shot durations from aligned words so B-roll cuts follow speech.

    Builds absolute cut points on the narration timeline so
    sum(shot.duration_sec) ~= last_word.end (no missing tail).
    """
    if not words or not script.shots:
        return words
    counts = [max(len(re.findall(r"\S+", s.dialogue or "")), 1) for s in script.shots]
    n = len(words)
    total_c = sum(counts) or 1
    # Cumulative word index ends for each shot (last shot always consumes remainder)
    ends: list[int] = []
    acc = 0
    for i, count in enumerate(counts):
        if i == len(counts) - 1:
            acc = n
        else:
            take = max(int(round(count * n / total_c)), 1)
            # leave at least 1 word for each remaining shot
            remaining_shots = len(counts) - i - 1
            acc = min(acc + take, n - remaining_shots)
            acc = max(acc, i + 1)
        ends.append(acc)

    prev_t = 0.0
    for shot, end_idx in zip(script.shots, ends):
        end_idx = max(min(end_idx, n), 1)
        end_t = float(words[end_idx - 1]["end"])
        shot.duration_sec = round(max(end_t - prev_t, 0.4), 2)
        prev_t = end_t
    # Ensure we cover through the true audio end
    last_end = float(words[-1]["end"])
    covered = sum(s.duration_sec for s in script.shots)
    if last_end > covered + 0.05:
        script.shots[-1].duration_sec = round(
            script.shots[-1].duration_sec + (last_end - covered), 2
        )
    script.recompute_duration()
    return words


def write_episode_ass_from_words(
    words: list[dict[str, Any]],
    out_path: Path,
    *,
    width: int,
    height: int,
    words_per_line: int = 4,
) -> Path:
    """Full-episode karaoke ASS using real word timestamps (audio-synced)."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not words:
        out_path.write_text("", encoding="utf-8")
        return out_path

    # Keep captions inside safe Shorts margins (UI + vertical crop).
    margin_v = int(height * 0.22)
    margin_x = 110
    font_size = 64
    primary = "&H00F0F0F0"
    secondary = "&H0088D7FF"
    outline = "&H00000000"
    back = "&H96000000"
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Karaoke,Arial Black,{font_size},{primary},{secondary},{outline},{back},-1,0,0,0,100,100,0,0,1,5,2,2,{margin_x},{margin_x},{margin_v},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = [header]
    # Shorter phrases so Turkish words don't clip at the edges.
    words_per_line = min(words_per_line, 3)
    for i in range(0, len(words), words_per_line):
        group = words[i : i + words_per_line]
        start = float(group[0]["start"])
        end = float(group[-1]["end"]) + 0.05
        body_parts = [r"{\fad(40,50)}"]
        for j, w in enumerate(group):
            dur = max(float(w["end"]) - float(w["start"]), 0.05)
            cs = max(int(round(dur * 100)), 5)
            body_parts.append(rf"{{\k{cs}}}{_escape_ass_text(str(w['word']))}")
            if j < len(group) - 1:
                body_parts.append(" ")
        body = "".join(body_parts)
        lines.append(
            f"Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},Karaoke,,0,0,0,,{body}\n"
        )

    out_path.write_text("".join(lines), encoding="utf-8")
    # also dump alignment for debugging
    meta_path = out_path.with_suffix(".words.json")
    meta_path.write_text(json.dumps(words, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
