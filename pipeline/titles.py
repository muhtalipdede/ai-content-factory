"""Title templates and light quality checks for Shorts CTR."""

from __future__ import annotations

import re

import config

# Prefer concrete outcomes over vague drama nouns
_VAGUE_TITLE = re.compile(
    r"(geleceğini tehdit|yanlış hesap|büyük skandal|şok gerçek|inanılmaz|meydan okuyan)"
    r"|(:\s*)?(karar|skandal|facia|çöküş)\s*$",
    re.IGNORECASE,
)

_CONCRETE = re.compile(
    r"\d|"
    r"koymadı|sakladı|yasakladı|battı|iflas|yalan|engelledi|reddetti|"
    r"gömdü|iptal|satmadı|gizledi|eritedi|kaçırdı|çaldı|uydurdu",
    re.IGNORECASE,
)


def title_templates() -> list[str]:
    raw = config.PROJECT.get("title_templates") or []
    return [str(t) for t in raw if str(t).strip()]


def pick_title(idea: dict, script_title: str | None = None) -> str:
    """Prefer explicit A/B picks from ideation, else script/idea title."""
    for key in ("preferred_title", "title_a", "title"):
        val = (idea.get(key) or "").strip()
        if val:
            return val[:100]
    if script_title and script_title.strip():
        return script_title.strip()[:100]
    return "Yeni Bölüm"


def thumb_text(title: str, *, max_chars: int = 42) -> str:
    """Short overlay line for thumbnail (claim after colon when present)."""
    t = (title or "").strip()
    if ":" in t:
        t = t.split(":", 1)[1].strip()
    t = re.sub(r"\s+", " ", t)
    if len(t) <= max_chars:
        return t
    cut = t[: max_chars - 1].rsplit(" ", 1)[0]
    return (cut or t[: max_chars - 1]) + "…"


def title_quality_errors(title: str) -> list[str]:
    """Soft CTR checks — only enforced when requires_sources projects ask for them."""
    errors: list[str] = []
    t = (title or "").strip()
    if len(t) < 28:
        errors.append("title too short for a concrete claim (<28 chars)")
    if len(t) > 100:
        errors.append("title exceeds YouTube soft limit (100)")
    if _VAGUE_TITLE.search(t) and not _CONCRETE.search(t):
        errors.append(
            "title looks vague — use a concrete claim "
            "(who + what happened), e.g. 'Kodak dijitali 18 yıl raflara koymadı'"
        )
    if not _CONCRETE.search(t) and ":" not in t:
        errors.append(
            "title needs a concrete claim (digit/outcome verb) "
            "or a claim-style 'Prefix: …' form"
        )
    return errors


def hook_is_question(dialogue: str) -> bool:
    return "?" in (dialogue or "")
