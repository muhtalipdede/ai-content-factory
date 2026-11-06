"""Generate episode ideas that avoid recent topics."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone

from openai import OpenAI

import config
from pipeline import state
from pipeline.brand import load_characters, safety_rules

logger = logging.getLogger(__name__)


def _client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for ideation.")
    return OpenAI(api_key=config.OPENAI_API_KEY)


def _new_episode_id() -> str:
    return f"ep_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def _fallback_idea() -> dict:
    fb = config.FALLBACK_IDEA or {}
    return {
        "episode_id": _new_episode_id(),
        "title": fb.get("title") or "Yeni Bölüm",
        "summary": fb.get("summary") or "Otonom üretilmiş özet.",
        "lesson": fb.get("lesson") or "",
        "hook": fb.get("hook") or "",
        "claim": fb.get("summary") or "",
        "title_a": fb.get("title") or "",
        "title_b": fb.get("title") or "",
        "preferred_title": fb.get("title") or "",
        "source_hint": "",
    }


def _reject_if_duplicate(idea: dict) -> None:
    hit = state.find_similar_topic(
        title=str(idea.get("title") or idea.get("preferred_title") or ""),
        claim=str(idea.get("claim") or idea.get("summary") or ""),
    )
    if hit:
        prior = hit.get("title") or hit.get("episode_id") or "prior episode"
        raise RuntimeError(
            f"Idea too similar to published topic: {prior!r} "
            f"(candidate={idea.get('title')!r})"
        )


def generate_idea(*, dry_run: bool = False, max_dedupe_retries: int = 2) -> dict:
    """Return a dict with episode_id, title, summary, lesson, hook (+ A/B titles)."""
    if dry_run or not config.OPENAI_API_KEY:
        idea = _fallback_idea()
        state.record_cost(0.0)
        return idea

    from pipeline.titles import pick_title, title_templates

    previous = state.previous_summaries()
    try:
        from pipeline import memory as learning_memory

        perf_hints = learning_memory.performance_hints(limit=5)
    except Exception:  # noqa: BLE001
        perf_hints = {"note": "no memory", "top_titles": [], "sample_size": 0}
    chars = load_characters()
    character_blurb = ", ".join(
        f"{c.get('display_name', cid)} ({cid})"
        for cid, c in (chars.get("characters") or {}).items()
    )
    system = config.IDEATION_SYSTEM or (
        "Sen YouTube Shorts için konu üreten bir yazarsın. Türkçe yaz. JSON döndür."
    )
    templates = title_templates()
    user = {
        "project": config.PROJECT.get("display_name") or config.PROJECT_ID,
        "speakers_mode": config.SPEAKERS_MODE,
        "characters": character_blurb,
        "avoid_summaries": previous,
        "performance_hints": perf_hints,
        "safety": safety_rules()[:1500],
        "format": "40-55 saniye Shorts",
        "title_templates": templates,
        "title_rules": [
            "Başlıkta somut claim olsun: kim + ne yaptı/ne olmadı (tercihen yıl veya sayı).",
            "Enron/Challenger gibi aşırı bilinen konulara 'kimse bilmiyor' deme.",
            "title_a ve title_b farklı A/B şablonlarından gelsin; preferred_title daha keskin olanı seç.",
            "hook mutlaka soru olsun (soru işareti ile bitir).",
            "claim tek cümlelik doğrulanabilir olgu olsun.",
        ],
        "return_schema": {
            "claim": "string — somut olgu",
            "title_a": "string — şablon A",
            "title_b": "string — şablon B",
            "preferred_title": "string — yayına gidecek başlık",
            "title": "string — preferred_title ile aynı",
            "summary": "string",
            "lesson": "string",
            "hook": "string — soru",
            "source_hint": "string — doğrulanabilir kaynak türü / kurum",
        },
    }
    client = _client()
    last_error: Exception | None = None
    for attempt in range(max_dedupe_retries + 1):
        repair = ""
        if last_error:
            repair = (
                "Önceki fikir geçmiş konulara çok benziyordu. "
                f"Farklı bir konu üret. ({last_error})"
            )
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0.9,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": (
                        "Yeni bir bölüm fikri üret. Önceki konulara benzer olma. "
                        "Başlık A/B üret; preferred_title'ı CTR için seç.\n"
                        + json.dumps(user, ensure_ascii=False)
                        + (("\n" + repair) if repair else "")
                    ),
                },
            ],
        )
        state.record_cost(config.EST_COST_SCRIPT * 0.4)
        content = response.choices[0].message.content or "{}"
        data = json.loads(content)
        fb = _fallback_idea()
        idea = {
            "episode_id": _new_episode_id(),
            "claim": (data.get("claim") or "").strip(),
            "title_a": (data.get("title_a") or "").strip(),
            "title_b": (data.get("title_b") or "").strip(),
            "preferred_title": (data.get("preferred_title") or data.get("title") or "").strip(),
            "title": "",
            "summary": data.get("summary") or fb["summary"],
            "lesson": data.get("lesson") or fb["lesson"],
            "hook": data.get("hook") or fb.get("hook") or "",
            "source_hint": (data.get("source_hint") or "").strip(),
        }
        idea["title"] = pick_title(idea, fb["title"])
        if not idea["preferred_title"]:
            idea["preferred_title"] = idea["title"]
        try:
            _reject_if_duplicate(idea)
            return idea
        except RuntimeError as exc:
            last_error = exc
            logger.warning("Ideation dedupe reject (attempt %d): %s", attempt + 1, exc)

    assert last_error is not None
    raise last_error
