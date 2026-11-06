"""Thumbnail and YouTube metadata packaging."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from pipeline.models import EpisodeMeta, EpisodeScript
from pipeline.titles import thumb_text


def _font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(text: str, max_chars: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur: list[str] = []
    for w in words:
        trial = (" ".join(cur + [w])).strip()
        if len(trial) <= max_chars:
            cur.append(w)
        else:
            if cur:
                lines.append(" ".join(cur))
            cur = [w]
    if cur:
        lines.append(" ".join(cur))
    return lines[:3]


def write_studio_checklist(
    script: EpisodeScript,
    episode_dir: Path,
    *,
    thumbnail_path: Path,
    title_a: str = "",
    title_b: str = "",
) -> Path:
    """Manual YouTube Studio steps (custom thumb often blocked by API)."""
    out = episode_dir / "studio_checklist.md"
    overlay = thumb_text(script.title)
    lines = [
        f"# Studio checklist — {script.episode_id}",
        "",
        f"**Yayın başlığı:** {script.title}",
        "",
        "## Başlık A/B",
        f"- A: {title_a or script.title}",
        f"- B: {title_b or '(yok — ideation A/B üretmediyse)'}",
        "- İlk 24–48 saatte CTR düşükse diğer başlığı Studio’dan dene.",
        "",
        "## Thumbnail",
        f"- Dosya: `{thumbnail_path.name}`",
        f"- Önerilen overlay metni: **{overlay}**",
        "- API özel kapak 403 verirse: Studio → Düzenle → Küçük resim yükle.",
        "- Tek güçlü görsel + 3–6 kelime; küçük yazı kullanma.",
        "",
        "## Yayın sonrası",
        "- Açıklamada kaynak URL’leri duruyor mu kontrol et.",
        "- `#Shorts` etiket/açıklamada var mı bak.",
        "- Pin yorum: “Hangisini bilmiyordun?”",
        (
            f"- Made for Kids: {'açık' if config.MADE_FOR_KIDS else 'kapalı'}"
            f" ({config.PROJECT.get('display_name') or config.PROJECT_ID})."
        ),
        "",
        "## Kaynaklar",
    ]
    for src in script.sources:
        lines.append(f"- {src}")
    if not script.sources:
        lines.append("- (yok)")
    report = episode_dir / "sources_report.json"
    if report.is_file():
        lines.extend(["", f"Kaynak raporu: `{report.name}`"])
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return out


def _cover_crop(base: Image.Image, tw: int, th: int) -> Image.Image:
    scale = max(tw / base.width, th / base.height)
    nw, nh = int(base.width * scale), int(base.height * scale)
    base = base.resize((nw, nh), Image.Resampling.LANCZOS)
    left = (nw - tw) // 2
    top = (nh - th) // 2
    return base.crop((left, top, left + tw, top + th))


def create_thumbnail(
    script: EpisodeScript,
    episode_dir: Path,
    hero_image: Path | None = None,
) -> Path:
    """Create YouTube thumbnail from first shot art + short claim text.

    Shorts shelf center-crops landscape thumbs and clips side text — so for
    VIDEO_FORMAT=shorts we export true 9:16 (1080x1920). Landscape stays 1280x720.
    """
    out = episode_dir / "thumbnail.jpg"
    shorts = str(config.VIDEO_FORMAT).lower() == "shorts"
    tw, th = (1080, 1920) if shorts else (1280, 720)

    if hero_image and hero_image.is_file():
        canvas = _cover_crop(Image.open(hero_image).convert("RGB"), tw, th)
    else:
        canvas = Image.new("RGB", (tw, th), (16, 24, 64))

    overlay = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    if shorts:
        # Lower third + side-safe margins (Shorts UI chrome eats edges)
        band_h = 520
        pad_x = 88
        draw.rectangle((0, th - band_h, tw, th), fill=(8, 12, 32, 220))
        claim = thumb_text(script.title, max_chars=48)
        title_lines = _wrap(claim, 14)
        font = _font(52)
        brand_font = _font(28)
        y = th - band_h + 48
        for line in title_lines[:4]:
            draw.text((pad_x, y), line, font=font, fill=(255, 230, 140, 255))
            y += 64
        brand_label = str(config.PROJECT.get("display_name") or config.PROJECT_ID or "")
        draw.text((pad_x, th - 72), brand_label, font=brand_font, fill=(180, 210, 255, 255))
    else:
        draw.rectangle((0, th - 220, tw, th), fill=(8, 12, 32, 200))
        claim = thumb_text(script.title, max_chars=36)
        title_lines = _wrap(claim, 22)
        font = _font(58)
        y = th - 200
        for line in title_lines:
            draw.text((40, y), line, font=font, fill=(255, 230, 140, 255))
            y += 66
        brand_font = _font(28)
        brand_label = str(config.PROJECT.get("display_name") or config.PROJECT_ID or "")
        draw.text((40, th - 48), brand_label, font=brand_font, fill=(180, 210, 255, 255))

    composed = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    composed.save(out, format="JPEG", quality=90, optimize=True)

    # Persist suggested overlay for Studio manual upload
    (episode_dir / "thumbnail_text.txt").write_text(claim + "\n", encoding="utf-8")
    return out


def build_metadata(
    script: EpisodeScript,
    episode_dir: Path,
    video_path: Path,
    thumbnail_path: Path,
    *,
    title_a: str = "",
    title_b: str = "",
) -> EpisodeMeta:
    brand_tags = list(config.PROJECT.get("default_tags") or [])
    if not brand_tags:
        brand_tags = ["Shorts"]
    tags = list(dict.fromkeys([*script.tags, *brand_tags, "Shorts"]))
    if config.VIDEO_FORMAT == "shorts" and "Shorts" not in script.description:
        description = script.description.rstrip() + "\n\n#Shorts"
    else:
        description = script.description

    meta = EpisodeMeta(
        episode_id=script.episode_id,
        title=script.title[:100],
        description=description[:4900],
        tags=tags[:500],
        made_for_kids=config.MADE_FOR_KIDS,
        privacy_status=config.YOUTUBE_PRIVACY,
        category_id=config.YOUTUBE_CATEGORY_ID,
        thumbnail_path=str(thumbnail_path),
        video_path=str(video_path),
    )
    (episode_dir / "metadata.json").write_text(
        meta.model_dump_json(indent=2),
        encoding="utf-8",
    )
    # Companion upload payload
    payload = {
        "snippet": {
            "title": meta.title,
            "description": meta.description,
            "tags": meta.tags,
            "categoryId": meta.category_id,
            "defaultLanguage": config.LANGUAGE,
        },
        "status": {
            "privacyStatus": meta.privacy_status,
            "selfDeclaredMadeForKids": meta.made_for_kids,
        },
    }
    if config.PUBLISH_AT:
        payload["status"]["privacyStatus"] = "private"
        payload["status"]["publishAt"] = config.PUBLISH_AT
    (episode_dir / "youtube_payload.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    write_studio_checklist(
        script,
        episode_dir,
        thumbnail_path=thumbnail_path,
        title_a=title_a,
        title_b=title_b,
    )
    return meta
