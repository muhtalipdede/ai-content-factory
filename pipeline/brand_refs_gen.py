"""Generate official-style character brand reference sheets into data/brand_refs/."""

from __future__ import annotations

import base64
import logging
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, ImageDraw, ImageFont

import config
from pipeline.brand import load_characters, style_suffix

logger = logging.getLogger(__name__)

# Landscape character design sheet size
SHEET_SIZE = (1536, 1024)


def _personality_blurb(character: dict[str, Any]) -> str:
    traits = character.get("personality") or []
    if not traits:
        return ""
    return ", ".join(traits[:5])


def sheet_prompt(character_id: str, character: dict[str, Any], *, compact: bool = False) -> str:
    name = character.get("display_name") or character_id
    role = (character.get("role") or "character").replace("_", " ").upper()
    visual = character.get("visual_prompt") or name
    if compact:
        # Shorter prompt for free endpoints / rate limits
        short_visual = visual
        if short_visual.lower().startswith("exact ") and "brand character" in short_visual.lower():
            short_visual = short_visual.split(":", 1)[-1].strip()
        short_visual = short_visual[:420]
        brand = str(config.PROJECT.get("display_name") or config.PROJECT_ID or "channel")
        return (
            f"{brand} character design sheet of {name} ({role}), "
            f"Pixar kids 3D style model pack layout: hero pose, turnaround, "
            f"expressions, palette swatches, soft studio background. "
            f"{short_visual}. No watermark."
        )

    negative = character.get("negative_prompt") or ""
    relation = character.get("relation") or ""
    blurb = _personality_blurb(character)
    palette = ", ".join(character.get("palette") or [])

    brand = str(config.PROJECT.get("display_name") or config.PROJECT_ID or "channel")
    return (
        f"Professional children's animation character design sheet for {brand}, "
        f"character {name}, subtitle {role}. "
        f"Layout like a Pixar model pack on a clean soft studio background: "
        f"large hero render top-left, four small cinematic mood panels top-right, "
        f"full-body turnaround front three-quarter side back bottom-left, "
        f"color palette swatches, expression grid, detail close-ups, small pose row. "
        f"Character design: {visual}. "
        f"Personality: {blurb}. {relation}. "
        f"Color palette accents: {palette}. "
        f"{style_suffix()}. "
        f"Readable English labels only for section titles (Name, Turnaround, Expressions, Details, Palette). "
        f"No gibberish text, no watermark, no logo clutter. "
        f"Avoid: {negative}"
    )


def _save_rgb(raw: bytes, out_path: Path, size: tuple[int, int] = SHEET_SIZE) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".tmp.png")
    tmp.write_bytes(raw)
    img = Image.open(tmp).convert("RGB")
    if img.size != size:
        img = img.resize(size, Image.Resampling.LANCZOS)
    img.save(out_path, format="PNG", optimize=True)
    tmp.unlink(missing_ok=True)
    return out_path


def generate_via_openai(prompt: str, out_path: Path) -> Path:
    from openai import OpenAI

    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY missing")
    client = OpenAI(api_key=config.OPENAI_API_KEY)
    # Closest landscape size to existing 1536x1024 sheets
    result = client.images.generate(
        model=config.OPENAI_IMAGE_MODEL,
        prompt=prompt[:3900],
        size="1792x1024",
        quality="standard",
        n=1,
        response_format="b64_json",
    )
    raw = base64.b64decode(result.data[0].b64_json)
    return _save_rgb(raw, out_path)


def generate_via_fal(prompt: str, out_path: Path) -> Path:
    if not config.FAL_KEY:
        raise RuntimeError("FAL_KEY missing")
    headers = {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}
    payload = {
        "prompt": prompt,
        "image_size": {"width": SHEET_SIZE[0], "height": SHEET_SIZE[1]},
        "num_images": 1,
        "enable_safety_checker": True,
    }
    with httpx.Client(timeout=180.0) as client:
        resp = client.post(
            "https://fal.run/fal-ai/flux/dev",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()
        url = resp.json()["images"][0]["url"]
        raw = client.get(url).content
    return _save_rgb(raw, out_path)


def generate_via_pollinations(prompt: str, out_path: Path) -> Path:
    """Free no-key Flux-style generation via Pollinations (with retries)."""
    attempts = [
        (SHEET_SIZE, "flux"),
        ((1280, 768), "flux"),
        ((1024, 768), "turbo"),
    ]
    last_err: Exception | None = None
    with httpx.Client(timeout=180.0, follow_redirects=True) as client:
        for attempt, ((w, h), model) in enumerate(attempts, start=1):
            encoded = urllib.parse.quote(prompt[:1200])
            url = (
                f"https://image.pollinations.ai/prompt/{encoded}"
                f"?width={w}&height={h}&model={model}&nologo=true&safe=true"
            )
            try:
                resp = client.get(url)
                if resp.status_code == 402:
                    logger.warning(
                        "Pollinations 402 (attempt %d/%d); waiting then retry…",
                        attempt,
                        len(attempts),
                    )
                    time.sleep(8 * attempt)
                    last_err = RuntimeError("Pollinations 402 Payment Required")
                    continue
                resp.raise_for_status()
                ctype = resp.headers.get("content-type", "")
                if "image" not in ctype and not resp.content.startswith(b"\x89PNG"):
                    raise RuntimeError(f"Pollinations did not return an image ({ctype})")
                return _save_rgb(resp.content, out_path)
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(3 * attempt)
    raise RuntimeError(f"Pollinations failed after retries: {last_err}")


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    h = value.strip().lstrip("#")
    if len(h) != 6:
        return (120, 140, 180)
    return tuple(int(h[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def generate_via_local(character_id: str, character: dict[str, Any], out_path: Path) -> Path:
    """Offline fallback: compose a simple brand sheet from palette + labels."""
    w, h = SHEET_SIZE
    palette = [_hex_to_rgb(p) for p in (character.get("palette") or [])] or [
        (30, 60, 120),
        (100, 180, 255),
        (244, 240, 230),
    ]
    bg = (248, 246, 242)
    img = Image.new("RGB", (w, h), bg)
    draw = ImageDraw.Draw(img)
    try:
        title_font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf", 64
        )
        body_font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 28
        )
        small_font = ImageFont.truetype(
            "/System/Library/Fonts/Supplemental/Arial.ttf", 22
        )
    except OSError:
        title_font = body_font = small_font = ImageFont.load_default()

    name = character.get("display_name") or character_id
    role = (character.get("role") or "").replace("_", " ").upper()
    primary = palette[0]
    accent = palette[min(1, len(palette) - 1)]

    # Hero panel
    draw.rounded_rectangle((48, 48, 620, 620), radius=28, fill=primary)
    draw.ellipse((180, 160, 480, 460), fill=accent)
    draw.ellipse((250, 230, 410, 390), fill=(255, 255, 255))
    draw.text((72, 540), name, fill=(255, 255, 255), font=title_font)
    draw.text((72, 610), role, fill=(230, 230, 230), font=body_font)

    # Mood panels
    x0 = 660
    for i in range(4):
        c = palette[i % len(palette)]
        box = (x0 + (i % 2) * 420, 48 + (i // 2) * 280, x0 + (i % 2) * 420 + 400, 48 + (i // 2) * 280 + 260)
        draw.rounded_rectangle(box, radius=20, fill=c)
        draw.text((box[0] + 20, box[1] + 20), f"Mood {i+1}", fill=(255, 255, 255), font=small_font)

    # Turnaround circles
    draw.text((48, 660), "TURNAROUND", fill=(40, 40, 40), font=body_font)
    for i in range(4):
        cx = 120 + i * 150
        cy = 820
        c = palette[i % len(palette)]
        draw.ellipse((cx - 50, cy - 50, cx + 50, cy + 50), fill=c)
        draw.text((cx - 20, cy + 60), ["F", "3/4", "S", "B"][i], fill=(40, 40, 40), font=small_font)

    # Palette row
    draw.text((700, 660), "PALETTE", fill=(40, 40, 40), font=body_font)
    for i, c in enumerate(palette[:8]):
        x = 700 + i * 90
        draw.rounded_rectangle((x, 720, x + 70, 790), radius=12, fill=c)

    # Personality
    blurb = _personality_blurb(character)
    draw.text((700, 830), blurb[:70], fill=(60, 60, 60), font=small_font)
    brand = str(config.PROJECT.get("display_name") or config.PROJECT_ID or "channel")
    draw.text((700, 870), f"{brand} · local brand sheet", fill=(120, 120, 120), font=small_font)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    return out_path


def resolve_provider(explicit: str | None = None) -> str:
    provider = (explicit or config.BRAND_REF_PROVIDER or "auto").strip().lower()
    if provider in {"openai", "fal", "pollinations", "local"}:
        return provider
    # auto: prefer configured paid keys, else free pollinations
    if config.FAL_KEY and config.IMAGE_PROVIDER == "fal":
        return "fal"
    if config.OPENAI_API_KEY:
        return "openai"
    if config.FAL_KEY:
        return "fal"
    return "pollinations"


def generate_character_sheet(
    character_id: str,
    *,
    force: bool = False,
    provider: str | None = None,
) -> Path | None:
    load_characters.cache_clear()
    chars = load_characters()["characters"]
    if character_id not in chars:
        raise KeyError(f"Unknown character: {character_id}")
    character = chars[character_id]
    out_path = config.PROJECT_ROOT / character.get(
        "reference_image", f"data/brand_refs/{character_id}.png"
    )
    if out_path.is_file() and out_path.stat().st_size > 0 and not force:
        logger.info("Skip %s (exists). Use --force to overwrite.", character_id)
        return None

    chosen = resolve_provider(provider)
    logger.info("Generating %s via %s → %s", character_id, chosen, out_path)

    if chosen == "openai":
        return generate_via_openai(sheet_prompt(character_id, character), out_path)
    if chosen == "fal":
        return generate_via_fal(sheet_prompt(character_id, character), out_path)
    if chosen == "local":
        return generate_via_local(character_id, character, out_path)
    if chosen == "pollinations":
        try:
            return generate_via_pollinations(
                sheet_prompt(character_id, character, compact=True), out_path
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Pollinations failed (%s); falling back to local sheet", exc)
            return generate_via_local(character_id, character, out_path)
    raise ValueError(f"Unknown brand-ref provider: {chosen}")


def missing_character_ids() -> list[str]:
    load_characters.cache_clear()
    missing = []
    for cid, c in load_characters()["characters"].items():
        path = config.PROJECT_ROOT / c.get("reference_image", f"data/brand_refs/{cid}.png")
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(cid)
    return missing


def generate_all(
    *,
    only_missing: bool = True,
    force: bool = False,
    character_ids: list[str] | None = None,
    provider: str | None = None,
) -> dict[str, str]:
    load_characters.cache_clear()
    all_ids = list(load_characters()["characters"].keys())
    if character_ids:
        targets = character_ids
    elif only_missing and not force:
        targets = missing_character_ids()
    else:
        targets = all_ids

    results: dict[str, str] = {}
    for cid in targets:
        try:
            path = generate_character_sheet(cid, force=force or not only_missing, provider=provider)
            results[cid] = str(path) if path else "skipped_exists"
        except Exception as exc:  # noqa: BLE001
            logger.exception("Failed %s: %s", cid, exc)
            results[cid] = f"error: {exc}"
    return results
