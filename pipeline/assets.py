"""Generate per-shot still images (and optional AI video clips)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import config
from pipeline.brand import (
    build_shot_prompt,
    character_negative,
    reference_paths_for,
)
from pipeline.hybrid_video import generate_clip
from pipeline.models import EpisodeScript, Shot
from pipeline import state

logger = logging.getLogger(__name__)


def _to_data_uri(path: Path) -> str:
    raw = path.read_bytes()
    mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    return f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"


def _placeholder_image(shot: Shot, out_path: Path, size: tuple[int, int]) -> Path:
    w, h = size
    img = Image.new("RGB", (w, h), (12, 18, 48))
    draw = ImageDraw.Draw(img)
    for y in range(h):
        shade = int(12 + (40 * y / h))
        draw.line([(0, y), (w, y)], fill=(shade, 20 + shade // 3, 60 + shade // 2))

    import random

    rng = random.Random(shot.shot_id)
    for _ in range(80):
        x, y = rng.randint(0, w - 1), rng.randint(0, h - 1)
        r = rng.randint(1, 3)
        draw.ellipse((x, y, x + r, y + r), fill=(230, 240, 255))

    # Paste brand reference sheets (scaled) so dry-runs prove refs are wired
    refs = reference_paths_for(shot.characters or ([shot.speaker] if shot.speaker != "narrator" else []))
    if refs:
        thumb_w = min(420, w // max(len(refs), 1) - 24)
        x0 = 40
        y0 = h // 2 - thumb_w // 3
        for path in refs[:4]:
            try:
                ref = Image.open(path).convert("RGB")
                # Prefer left hero area of design sheets
                crop_w = int(ref.width * 0.42)
                crop_h = int(ref.height * 0.55)
                hero = ref.crop((0, 0, crop_w, crop_h))
                hero.thumbnail((thumb_w, int(thumb_w * 1.2)), Image.Resampling.LANCZOS)
                img.paste(hero, (x0, y0))
                x0 += hero.width + 16
            except OSError:
                logger.warning("Could not load brand ref %s", path)
    else:
        # Deterministic placeholder colors when brand refs are missing
        palette = [
            (80, 160, 255),
            (255, 160, 60),
            (180, 100, 255),
            (255, 140, 80),
            (126, 231, 245),
            (201, 160, 255),
            (255, 209, 102),
            (184, 179, 200),
            (124, 179, 66),
            (255, 77, 154),
        ]
        for i, cid in enumerate(shot.characters or [shot.speaker]):
            c = palette[hash(cid) % len(palette)]
            cx, cy = 120 + i * 160, h - 280
            draw.ellipse((cx, cy, cx + 100, cy + 100), fill=c)

    label = f"{shot.shot_id}\n{shot.speaker}"
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.multiline_text((48, 80), label, fill=(255, 220, 120), font=font, spacing=12)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG")
    return out_path


def _openai_image(prompt: str, negative: str, out_path: Path, size: tuple[int, int]) -> Path:
    import httpx
    from openai import OpenAI

    client = OpenAI(api_key=config.OPENAI_API_KEY)
    model = config.OPENAI_IMAGE_MODEL
    portrait = size[1] > size[0]
    # dall-e-3 uses 1024x1792; gpt-image-* family uses 1024x1536 (portrait)
    if model.startswith("dall-e"):
        api_size = "1024x1792" if portrait else "1792x1024"
    else:
        api_size = "1024x1536" if portrait else "1536x1024"
    full_prompt = f"{prompt}. Avoid: {negative}"
    kwargs: dict = {
        "model": model,
        "prompt": full_prompt[:3900],
        "size": api_size,
        "n": 1,
    }
    # dall-e-3 accepts quality; newer image models may reject unknown fields
    if model.startswith("dall-e"):
        kwargs["quality"] = "standard"

    try:
        result = client.images.generate(**kwargs, response_format="b64_json")
    except TypeError:
        result = client.images.generate(**kwargs)
    except Exception as exc:  # noqa: BLE001
        # Some API versions reject response_format entirely
        if "response_format" in str(exc):
            result = client.images.generate(**kwargs)
        else:
            raise

    item = result.data[0]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = out_path.with_suffix(".gen.png")
    if getattr(item, "b64_json", None):
        tmp.write_bytes(base64.b64decode(item.b64_json))
    elif getattr(item, "url", None):
        with httpx.Client(timeout=120.0) as http:
            tmp.write_bytes(http.get(item.url).content)
    else:
        raise RuntimeError("OpenAI image response missing b64_json and url")

    img = Image.open(tmp).convert("RGB")
    img = img.resize(size, Image.Resampling.LANCZOS)
    img.save(out_path, format="PNG")
    tmp.unlink(missing_ok=True)
    return out_path


def _fal_image(
    prompt: str,
    negative: str,
    out_path: Path,
    size: tuple[int, int],
    ref_paths: list[Path] | None = None,
) -> Path:
    import httpx

    if not config.FAL_KEY:
        raise RuntimeError("FAL_KEY required for IMAGE_PROVIDER=fal")
    headers = {"Authorization": f"Key {config.FAL_KEY}", "Content-Type": "application/json"}

    # Prefer identity-preserving generation when brand refs exist
    use_refs = bool(config.IMAGE_USE_REFS and ref_paths)
    if use_refs:
        endpoint = "https://fal.run/fal-ai/flux-pulid"
        payload = {
            "prompt": prompt,
            "negative_prompt": negative,
            "reference_images": [{"image_url": _to_data_uri(ref_paths[0])}],
            "image_size": {"width": size[0], "height": size[1]},
            "num_images": 1,
            "id_weight": 0.8,
        }
    else:
        endpoint = "https://fal.run/fal-ai/flux/dev"
        payload = {
            "prompt": prompt,
            "negative_prompt": negative,
            "image_size": {"width": size[0], "height": size[1]},
            "num_images": 1,
        }

    with httpx.Client(timeout=180.0) as client:
        resp = client.post(endpoint, headers=headers, json=payload)
        if resp.status_code >= 400 and use_refs:
            logger.warning(
                "fal PuLID failed (%s); falling back to flux/dev text-only",
                resp.status_code,
            )
            endpoint = "https://fal.run/fal-ai/flux/dev"
            payload = {
                "prompt": prompt,
                "negative_prompt": negative,
                "image_size": {"width": size[0], "height": size[1]},
                "num_images": 1,
            }
            resp = client.post(endpoint, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
        url = data["images"][0]["url"]
        img_bytes = client.get(url).content

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(img_bytes)
    img = Image.open(out_path).convert("RGB").resize(size, Image.Resampling.LANCZOS)
    img.save(out_path, format="PNG")
    return out_path


def generate_shot_image(
    shot: Shot,
    out_path: Path,
    *,
    dry_run: bool = False,
) -> Path:
    size = config.frame_size()
    char_ids = shot.characters or ([shot.speaker] if shot.speaker != "narrator" else [])
    prompt = build_shot_prompt(shot.visual_prompt, char_ids)
    negatives = [character_negative(c) for c in char_ids] or [
        "text, watermark, violence, horror"
    ]
    negative = ", ".join(negatives)
    refs = reference_paths_for(char_ids)

    if dry_run or config.IMAGE_PROVIDER == "placeholder" or (
        config.IMAGE_PROVIDER == "openai" and not config.OPENAI_API_KEY
    ):
        return _placeholder_image(shot, out_path, size)

    if config.IMAGE_PROVIDER == "openai":
        if refs:
            logger.info(
                "OpenAI DALL·E has no native ref lock; embedding brand identity in prompt (%d refs)",
                len(refs),
            )
        path = _openai_image(prompt, negative, out_path, size)
        state.record_cost(config.EST_COST_IMAGE)
        return path
    if config.IMAGE_PROVIDER == "fal":
        path = _fal_image(prompt, negative, out_path, size, ref_paths=refs)
        state.record_cost(config.EST_COST_IMAGE)
        return path
    if config.IMAGE_PROVIDER == "replicate":
        logger.warning("replicate image provider not fully wired; using placeholder")
        return _placeholder_image(shot, out_path, size)

    return _placeholder_image(shot, out_path, size)


def generate_assets(
    script: EpisodeScript,
    episode_dir: Path,
    *,
    dry_run: bool = False,
) -> dict[str, dict[str, str | None]]:
    """Generate images (and optional clips). Returns map shot_id -> paths."""
    images_dir = episode_dir / "images"
    clips_dir = episode_dir / "clips"
    images_dir.mkdir(parents=True, exist_ok=True)
    clips_dir.mkdir(parents=True, exist_ok=True)

    from pipeline.brand import brand_refs_exist

    refs = brand_refs_exist()
    logger.info("Brand refs available: %d", len(refs))

    manifest: dict[str, dict[str, str | None]] = {}
    for shot in script.shots:
        img_path = images_dir / f"{shot.shot_id}.png"
        generate_shot_image(shot, img_path, dry_run=dry_run)
        clip_path: Path | None = None
        if shot.motion == "ai_video":
            candidate = clips_dir / f"{shot.shot_id}.mp4"
            clip_path = generate_clip(shot, img_path, candidate, dry_run=dry_run)
            if clip_path:
                state.record_cost(config.EST_COST_AI_VIDEO)
        manifest[shot.shot_id] = {
            "image": str(img_path),
            "clip": str(clip_path) if clip_path else None,
        }
    (episode_dir / "assets.json").write_text(
        __import__("json").dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest
