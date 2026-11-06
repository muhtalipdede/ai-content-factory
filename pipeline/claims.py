"""Claim extraction and lightweight evidence overlap gate.

This is not a full NLI fact-checker. It extracts material claims from the script,
fetches source page text, and requires token/entity overlap before publish spend.
A reachable URL alone is not treated as proof.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from html import unescape
from pathlib import Path
from typing import Any

import httpx

import config
from pipeline.models import EpisodeScript
from pipeline.state import normalize_topic_key, token_jaccard

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>|<[^>]+>", re.I)
_SPACE_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?؟])\s+|\n+")
_OUTCOME = re.compile(
    r"\d|"
    r"icat|keşfet|yasak|battı|iflas|engelle|reddet|gizle|satmad|koymad|"
    r"iptal|erited|kaçır|uydur|görüntü|ölçül|keşf",
    re.IGNORECASE,
)


def html_to_text(html: str, *, max_chars: int = 50000) -> str:
    text = _TAG_RE.sub(" ", html or "")
    text = unescape(text)
    text = _SPACE_RE.sub(" ", text).strip()
    return text[:max_chars]


def fetch_source_text(url: str, *, timeout: float = 15.0) -> tuple[bool, str, str]:
    """Return (ok, text_or_empty, reason)."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            resp = client.get(url)
        if resp.status_code >= 400 and resp.status_code not in {401, 403, 429}:
            return False, "", f"bad status {resp.status_code}"
        ctype = (resp.headers.get("content-type") or "").lower()
        if "html" not in ctype and "text" not in ctype and "xml" not in ctype:
            # Soft: still try decode
            pass
        text = html_to_text(resp.text or "")
        if len(text) < 80:
            return False, text, "page text too short / likely blocked"
        return True, text, f"fetched ({resp.status_code}, {len(text)} chars)"
    except httpx.HTTPError as exc:
        return False, "", f"request failed: {exc.__class__.__name__}"


def extract_claims(script: EpisodeScript, *, idea_claim: str = "") -> list[dict[str, str]]:
    """Extract independently checkable claim strings (heuristic, no LLM)."""
    claims: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(text: str, source_field: str) -> None:
        cleaned = _SPACE_RE.sub(" ", (text or "").strip())
        if len(cleaned) < 12:
            return
        key = normalize_topic_key(cleaned)
        if not key or key in seen:
            return
        # Prefer factual-looking strings
        if source_field == "dialogue" and not _OUTCOME.search(cleaned):
            return
        seen.add(key)
        claims.append(
            {
                "claim_id": f"c{len(claims)+1:02d}",
                "text": cleaned[:300],
                "source_field": source_field,
            }
        )

    if idea_claim:
        _add(idea_claim, "idea_claim")
    title = (script.title or "").strip()
    if ":" in title:
        _add(title.split(":", 1)[1].strip(), "title")
    else:
        _add(title, "title")
    _add(script.summary or "", "summary")
    _add(script.lesson or "", "lesson")

    for shot in script.shots[1:]:  # skip hook question
        for part in _SENTENCE_RE.split(shot.dialogue or ""):
            _add(part, "dialogue")
            if len(claims) >= 6:
                break
        if len(claims) >= 6:
            break

    if not claims and title:
        _add(title, "title")
    return claims


def _best_passage(claim: str, page_text: str, *, window: int = 220) -> str:
    tokens = [t for t in normalize_topic_key(claim).split() if len(t) > 3]
    if not tokens or not page_text:
        return page_text[:window]
    lower = page_text.lower()
    best_idx = -1
    for tok in tokens:
        idx = lower.find(tok.lower())
        if idx >= 0:
            best_idx = idx
            break
    if best_idx < 0:
        return page_text[:window]
    start = max(0, best_idx - 40)
    return page_text[start : start + window].strip()


def score_claim_against_text(claim: str, page_text: str) -> dict[str, Any]:
    claim_key = normalize_topic_key(claim)
    page_key = normalize_topic_key(page_text[:20000])
    overlap = token_jaccard(claim_key, page_key)
    claim_tokens = set(claim_key.split())
    page_tokens = set(page_key.split())
    hit_tokens = sorted(claim_tokens & page_tokens)
    # Years / distinctive tokens: require at least 2 content hits or solid jaccard
    years = {t for t in claim_tokens if t.isdigit() and len(t) == 4}
    year_ok = bool(years) and years.issubset(page_tokens)
    supported = overlap >= 0.12 or len(hit_tokens) >= 3 or (year_ok and len(hit_tokens) >= 2)
    status = "supported" if supported else "unsupported"
    return {
        "overlap": round(overlap, 4),
        "hit_tokens": hit_tokens[:20],
        "year_ok": year_ok,
        "status": status,
        "passage": _best_passage(claim, page_text) if supported else "",
    }


def verify_claims(
    script: EpisodeScript,
    *,
    idea_claim: str = "",
    episode_dir: Path | None = None,
    fetch: bool = True,
    page_texts: dict[str, str] | None = None,
) -> tuple[bool, dict[str, Any]]:
    """Verify claims against source pages. Returns (ok, report)."""
    claims = extract_claims(script, idea_claim=idea_claim)
    sources = [s.strip() for s in script.sources if str(s).strip()]
    pages: dict[str, dict[str, Any]] = {}
    page_texts = dict(page_texts or {})

    for url in sources:
        if url in page_texts:
            pages[url] = {"ok": True, "reason": "injected", "chars": len(page_texts[url])}
            continue
        if not fetch:
            pages[url] = {"ok": False, "reason": "fetch disabled", "chars": 0}
            continue
        ok, text, reason = fetch_source_text(url)
        pages[url] = {"ok": ok, "reason": reason, "chars": len(text)}
        if ok:
            page_texts[url] = text

    claim_rows: list[dict[str, Any]] = []
    supported_count = 0
    for claim in claims:
        evidence: list[dict[str, Any]] = []
        best_status = "unsupported"
        for url, text in page_texts.items():
            scored = score_claim_against_text(claim["text"], text)
            evidence.append(
                {
                    "url": url,
                    "status": scored["status"],
                    "overlap": scored["overlap"],
                    "hit_tokens": scored["hit_tokens"],
                    "passage": scored["passage"],
                    "retrieved_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            if scored["status"] == "supported":
                best_status = "supported"
        if best_status == "supported":
            supported_count += 1
        claim_rows.append(
            {
                **claim,
                "status": best_status,
                "evidence": evidence,
            }
        )

    # Gate: at least one material claim must be supported; none may be contradicted
    # (contradiction not implemented yet — unsupported primary title/summary fails).
    primary_ids = {"idea_claim", "title", "summary"}
    primary = [c for c in claim_rows if c.get("source_field") in primary_ids]
    if not primary:
        primary = claim_rows[:1]
    primary_ok = any(c.get("status") == "supported" for c in primary)
    ok = bool(claims) and primary_ok and supported_count >= 1

    report: dict[str, Any] = {
        "ok": ok,
        "supported_count": supported_count,
        "claim_count": len(claim_rows),
        "pages": pages,
        "claims": claim_rows,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    if episode_dir is not None:
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "claims_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    return ok, report


def verify_claims_gate(
    script: EpisodeScript,
    *,
    idea_claim: str = "",
    episode_dir: Path | None = None,
    fetch: bool = True,
    page_texts: dict[str, str] | None = None,
) -> tuple[bool, list[str]]:
    ok, report = verify_claims(
        script,
        idea_claim=idea_claim,
        episode_dir=episode_dir,
        fetch=fetch,
        page_texts=page_texts,
    )
    errors: list[str] = []
    if not report.get("claims"):
        errors.append("no extractable claims")
    for row in report.get("claims") or []:
        if row.get("source_field") in {"idea_claim", "title", "summary"} and row.get(
            "status"
        ) != "supported":
            errors.append(
                f"{row.get('claim_id')}: unsupported primary claim — {row.get('text')!r}"
            )
    if not ok and not errors:
        errors.append("claim evidence gate failed")
    return ok, errors
