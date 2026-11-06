"""Expand an idea into a shot-level EpisodeScript."""

from __future__ import annotations

import json

from openai import OpenAI

import config
from pipeline import state
from pipeline.brand import load_characters, safety_rules
from pipeline.models import EpisodeScript, Shot
from pipeline.safety import sanitize_dialogue, validate_script


def _client() -> OpenAI:
    if not config.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is required for scripting.")
    return OpenAI(api_key=config.OPENAI_API_KEY)


def _default_description(idea: dict, sources: list[str] | None = None) -> str:
    parts = [idea.get("summary") or ""]
    if idea.get("lesson"):
        parts.append(f"Ders: {idea['lesson']}")
    if sources:
        parts.append("Kaynaklar:\n" + "\n".join(f"- {s}" for s in sources))
    footer = config.DESCRIPTION_FOOTER or ""
    if footer:
        parts.append(footer)
    return "\n\n".join(p for p in parts if p)


def _fallback_script(idea: dict) -> EpisodeScript:
    if config.SPEAKERS_MODE == "narrator_only":
        shots = [
            Shot(
                shot_id="s01",
                duration_sec=5,
                speaker="narrator",
                dialogue=(
                    idea.get("hook")
                    if idea.get("hook") and "?" in str(idea.get("hook"))
                    else "Dijital kamerayı icat eden şirket neden satmadı?"
                ),
                visual_prompt="cinematic archival folder under moody teal light, no text",
                characters=[],
                motion="pan",
            ),
            Shot(
                shot_id="s02",
                duration_sec=10,
                speaker="narrator",
                dialogue=idea.get("summary") or "Bilinen hikaye böyleydi.",
                visual_prompt="wide documentary still of an empty archival room",
                characters=[],
                motion="still",
            ),
            Shot(
                shot_id="s03",
                duration_sec=12,
                speaker="narrator",
                dialogue="Asıl ayrıntı belgelerde saklıydı.",
                visual_prompt="close-up of stylized documents and a desk lamp, cinematic grain",
                characters=[],
                motion="pan",
            ),
            Shot(
                shot_id="s04",
                duration_sec=10,
                speaker="narrator",
                dialogue=idea.get("lesson") or "Kanıtlar bunu destekliyor.",
                visual_prompt="abstract metaphor of a sealed door cracking open with soft light",
                characters=[],
                motion="still",
            ),
            Shot(
                shot_id="s05",
                duration_sec=8,
                speaker="narrator",
                dialogue="Asıl garip olan şu: ders hâlâ geçerli. Kaynaklar açıklamada.",
                visual_prompt="fading timeline graphic dissolving into darkness, no readable text",
                characters=[],
                motion="pan",
            ),
        ]
        sources = [
            "https://www.nytimes.com/2015/08/13/technology/personaltech/an-invention-that-changed-photography.html",
            "https://en.wikipedia.org/wiki/Steve_Sasson",
        ]
    else:
        shots = [
            Shot(
                shot_id="s01",
                duration_sec=8,
                speaker="narrator",
                dialogue="Hello little explorer! Our friends are ready for a new adventure.",
                visual_prompt="friendly cartoon heroes standing together on a colorful planet under a starry sky, welcoming pose",
                characters=["hero", "sidekick", "guide"],
                motion="pan",
            ),
            Shot(
                shot_id="s02",
                duration_sec=10,
                speaker="hero",
                dialogue="Look! The star dust is missing. We can find it together!",
                visual_prompt="cartoon hero pointing at faint sparkling dust trails across rocky ground",
                characters=["hero"],
                motion="still",
            ),
            Shot(
                shot_id="s03",
                duration_sec=10,
                speaker="sidekick",
                dialogue="My sensors show a glow this way!",
                visual_prompt="cute robot sidekick with a glowing digital face scanning a glowing dust path",
                characters=["sidekick"],
                motion="pan",
            ),
            Shot(
                shot_id="s04",
                duration_sec=10,
                speaker="guide",
                dialogue="I will help too. Look, there is light inside the cave!",
                visual_prompt="friendly alien guide peeking into a softly glowing cave entrance",
                characters=["guide"],
                motion="still",
            ),
            Shot(
                shot_id="s05",
                duration_sec=12,
                speaker="hero",
                dialogue="We did it together! The stars shine again. You are an explorer too!",
                visual_prompt="cartoon heroes celebrating as stars brighten above the planet, warm joyful ending",
                characters=["hero", "sidekick", "guide"],
                motion="ai_video",
            ),
        ]
        sources = []

    script = EpisodeScript(
        episode_id=idea["episode_id"],
        title=idea["title"],
        summary=idea["summary"],
        lesson=idea.get("lesson", ""),
        tags=list(config.DEFAULT_TAGS or ["shorts"]),
        description=_default_description(idea, sources),
        sources=sources,
        shots=shots,
    )
    script.recompute_duration()
    return script


def _limit_ai_video_shots(script: EpisodeScript) -> EpisodeScript:
    count = 0
    for shot in script.shots:
        if shot.motion == "ai_video":
            count += 1
            if count > config.MAX_AI_VIDEO_SHOTS:
                shot.motion = "pan"
            else:
                shot.duration_sec = min(shot.duration_sec, config.AI_VIDEO_MAX_SEC + 2)
    return script


def repair_script_sources(
    script: EpisodeScript,
    source_errors: list[str],
) -> EpisodeScript:
    """Ask the model once for replacement https sources after HTTP gate failure."""
    if not config.OPENAI_API_KEY:
        raise RuntimeError("Cannot repair sources without OPENAI_API_KEY")
    client = _client()
    response = client.chat.completions.create(
        model=config.OPENAI_MODEL,
        temperature=0.2,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": (
                    "Sen fact-check editörüsün. Sadece JSON döndür: "
                    '{"sources":["https://...","https://..."]}. '
                    "Kaynaklar gerçek, erişilebilir, claim'i destekleyen https URL olmalı. "
                    "example.com, uydurma path, kırık link yasak. "
                    "Wikipedia, Britannica, resmi kurum, saygın gazete tercih et."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "title": script.title,
                        "summary": script.summary,
                        "failed_sources": script.sources,
                        "errors": source_errors,
                        "need_at_least": max(1, int(config.MIN_SOURCES)),
                    },
                    ensure_ascii=False,
                ),
            },
        ],
    )
    state.record_cost(config.EST_COST_SCRIPT * 0.3)
    raw = json.loads(response.choices[0].message.content or "{}")
    sources = [str(s).strip() for s in (raw.get("sources") or []) if str(s).strip()]
    if len(sources) < max(1, int(config.MIN_SOURCES)):
        raise RuntimeError(f"Source repair returned too few URLs: {sources}")
    script.sources = sources
    # Refresh description source list when present.
    if "Kaynaklar:" in (script.description or ""):
        head = script.description.split("Kaynaklar:")[0].rstrip()
        script.description = (
            head
            + "\n\nKaynaklar:\n"
            + "\n".join(f"- {s}" for s in sources)
            + (
                f"\n\n{config.DESCRIPTION_FOOTER}"
                if config.DESCRIPTION_FOOTER
                and config.DESCRIPTION_FOOTER not in head
                else ""
            )
        )
    return script


def generate_script(idea: dict, *, dry_run: bool = False, max_retries: int = 2) -> EpisodeScript:
    if dry_run or not config.OPENAI_API_KEY:
        script = _fallback_script(idea)
        ok, errors = validate_script(script)
        if not ok:
            raise RuntimeError(f"Fallback script failed safety: {errors}")
        return _limit_ai_video_shots(script)

    chars = load_characters()
    system = config.SCRIPT_SYSTEM or (
        "Sen YouTube Shorts için senaryo yazarsın. Sadece JSON döndür. "
        "Diyalog Türkçe, visual_prompt İngilizce. motion: still|pan|ai_video."
    )
    from pipeline.brand import allowed_speakers as brand_speakers
    from pipeline.titles import pick_title, title_templates

    schema_hint = {
        "title": "string — somut claim; idea.preferred_title veya title_a/b'den seç",
        "summary": "string",
        "lesson": "string",
        "tags": ["string"],
        "description": "string",
        "sources": ["https://... gerçek URL — en az 2 tercih"],
        "shots": [
            {
                "shot_id": "s01",
                "duration_sec": 8,
                "speaker": "character_id|narrator",
                "dialogue": "İlk shot ZORUNLU hook SORUSU (? ile)",
                "visual_prompt": "English scene description",
                "characters": [],
                "motion": "still|pan|ai_video",
                "sfx": "optional",
            }
        ],
    }

    last_errors: list[str] = []
    client = _client()
    for attempt in range(max_retries + 1):
        repair = ""
        if last_errors:
            repair = "Önceki taslak reddedildi. Düzelt:\n" + "\n".join(last_errors)
        response = client.chat.completions.create(
            model=config.OPENAI_MODEL,
            temperature=0.7,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "idea": idea,
                            "speakers_mode": config.SPEAKERS_MODE,
                            "requires_sources": config.REQUIRES_SOURCES,
                            "title_templates": title_templates(),
                            "script_rules": [
                                "title: somut claim (kim + ne oldu); belirsiz 'skandal/karar' başlık yok",
                                "s01.dialogue: hook SORUSU (? zorunlu)",
                                "sources: çalışan https URL; example.com yok; claim'i desteklemeli",
                                "Aşırı meşhur olaylarda 'kimse bilmiyor' iddiası kullanma",
                            ],
                            "characters": chars.get("characters") or {},
                            "safety": safety_rules()[:2000],
                            "schema": schema_hint,
                            "repair": repair,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
        )
        state.record_cost(config.EST_COST_SCRIPT * 0.6)
        raw = json.loads(response.choices[0].message.content or "{}")
        speakers = brand_speakers()
        allowed_motion = {"still", "pan", "ai_video"}
        shots = []
        for i, s in enumerate(raw.get("shots") or [], start=1):
            speaker = str(s.get("speaker") or "narrator").strip().lower()
            if config.SPEAKERS_MODE == "narrator_only":
                speaker = "narrator"
            elif speaker not in speakers:
                speaker = "narrator"
            motion = str(s.get("motion") or "still").strip().lower()
            if motion not in allowed_motion:
                motion = "still"
            shot_chars = [str(c).strip().lower() for c in (s.get("characters") or [])]
            shot_chars = [c for c in shot_chars if c in speakers and c != "narrator"]
            if config.SPEAKERS_MODE == "narrator_only":
                shot_chars = []
            shots.append(
                Shot(
                    shot_id=s.get("shot_id") or f"s{i:02d}",
                    duration_sec=float(s.get("duration_sec") or 8),
                    speaker=speaker,
                    dialogue=sanitize_dialogue(s.get("dialogue") or ""),
                    visual_prompt=(s.get("visual_prompt") or "").strip(),
                    characters=shot_chars,
                    motion=motion,  # type: ignore[arg-type]
                    sfx=s.get("sfx"),
                )
            )
        sources = [str(s).strip() for s in (raw.get("sources") or []) if str(s).strip()]
        title = pick_title(idea, (raw.get("title") or idea.get("title") or "").strip())
        # Prefer model title when it already carries a concrete claim colon form
        model_title = (raw.get("title") or "").strip()
        if model_title and (":" in model_title or len(model_title) >= 28):
            title = model_title[:100]
        if shots and idea.get("hook") and "?" not in (shots[0].dialogue or ""):
            shots[0].dialogue = sanitize_dialogue(str(idea["hook"]))
            if "?" not in shots[0].dialogue:
                shots[0].dialogue = shots[0].dialogue.rstrip(".!") + "?"
        script = EpisodeScript(
            episode_id=idea["episode_id"],
            title=title,
            summary=(raw.get("summary") or idea["summary"]).strip(),
            lesson=(raw.get("lesson") or idea.get("lesson", "")).strip(),
            tags=list(raw.get("tags") or config.DEFAULT_TAGS or ["shorts"]),
            description=(raw.get("description") or _default_description(idea, sources)).strip(),
            sources=sources,
            shots=shots,
        )
        if sources and "http" not in script.description.lower():
            script.description = (
                script.description.rstrip()
                + "\n\nKaynaklar:\n"
                + "\n".join(f"- {s}" for s in sources)
            )
        script.recompute_duration()
        script = _limit_ai_video_shots(script)
        ok, errors = validate_script(script)
        if ok:
            return script
        last_errors = errors

    raise RuntimeError(
        "Script generation failed validation after retries; "
        "refusing wrong-topic fallback.\n"
        + "\n".join(last_errors or ["(no validation details)"])
    )


def save_script(script: EpisodeScript, episode_dir) -> None:
    path = episode_dir / "script.json"
    path.write_text(script.model_dump_json(indent=2), encoding="utf-8")
