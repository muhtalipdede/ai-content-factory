"""Content safety gate for scripts (project-aware)."""

from __future__ import annotations

import re

import config
from pipeline.models import EpisodeScript, Shot

# Kids / general hostility patterns (always applied lightly)
BLOCK_PATTERNS_KIDS = [
    r"\b(öldür|silah|kan|korkunç|dehşet|kaçın|döv|vur|bomba|patla)\b",
    r"\b(kill|gun|blood|horror|weapon|die|dead|shoot)\b",
    r"\b(aptall?|salak|gerizekal)\b",
]

# Adult dark-fact: block harmful advice / conspiracy bait, not historical words
BLOCK_PATTERNS_ADULT = [
    r"\b(seni uyandır|uyan ey|gizlediler ama|doktorlar saklıyor)\b",
    r"\b(garantili kazanç|mutlaka al|şu hisseyi)\b",
    r"\b(mucize tedavi|kanseri bitir|doktorlar istemiyor)\b",
]


def shot_violations(shot: Shot) -> list[str]:
    text = f"{shot.dialogue} {shot.visual_prompt}".lower()
    patterns = BLOCK_PATTERNS_KIDS if config.MADE_FOR_KIDS else BLOCK_PATTERNS_ADULT
    hits: list[str] = []
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            hits.append(pattern)
    return hits


def validate_script(script: EpisodeScript) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not script.shots:
        errors.append("Script has no shots.")
    if not script.title.strip():
        errors.append("Missing title.")
    total = sum(s.duration_sec for s in script.shots)
    min_sec = float(config.SCRIPT_MIN_DURATION_SEC)
    max_sec = float(config.SCRIPT_MAX_DURATION_SEC)
    if total < min_sec:
        errors.append(f"Too short: {total:.1f}s (min {min_sec:.0f}s)")
    if total > max_sec:
        errors.append(f"Too long for Shorts: {total:.1f}s (max {max_sec:.0f}s)")

    if config.REQUIRES_SOURCES:
        from pipeline.sources import format_ok
        from pipeline.titles import hook_is_question, title_quality_errors

        sources = [s.strip() for s in script.sources if str(s).strip()]
        min_sources = max(1, int(config.MIN_SOURCES))
        if len(sources) < min_sources:
            errors.append(
                f"requires_sources: need at least {min_sources} source URL(s), got {len(sources)}"
            )
        for src in sources:
            ok, reason = format_ok(src)
            if not ok:
                errors.append(f"source: {reason}")
        errors.extend(title_quality_errors(script.title))
        if script.shots and not hook_is_question(script.shots[0].dialogue):
            errors.append("s01: first shot must be a hook question (include '?')")

    if config.SPEAKERS_MODE == "narrator_only":
        for shot in script.shots:
            if shot.speaker != "narrator":
                errors.append(f"{shot.shot_id}: narrator_only but speaker={shot.speaker}")
            if shot.characters:
                errors.append(f"{shot.shot_id}: narrator_only but characters set")

    for shot in script.shots:
        for hit in shot_violations(shot):
            errors.append(f"{shot.shot_id}: blocked pattern {hit}")
        if not shot.dialogue.strip() and shot.speaker != "narrator":
            errors.append(f"{shot.shot_id}: empty dialogue")
        if not shot.visual_prompt.strip():
            errors.append(f"{shot.shot_id}: empty visual_prompt")
    return (len(errors) == 0), errors


def sanitize_dialogue(text: str) -> str:
    cleaned = text.strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned
