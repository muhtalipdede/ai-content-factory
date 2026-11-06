"""Pre-publish source URL verification gate."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {
    "example.com",
    "example.org",
    "localhost",
    "127.0.0.1",
}


def _normalize(url: str) -> str:
    return (url or "").strip()


def format_ok(url: str) -> tuple[bool, str]:
    u = _normalize(url)
    if not u:
        return False, "empty source"
    parsed = urlparse(u)
    if parsed.scheme not in {"http", "https"}:
        return False, f"not an http(s) URL: {u}"
    host = (parsed.hostname or "").lower()
    if not host:
        return False, f"missing host: {u}"
    if host in _BLOCKED_HOSTS or host.endswith(".example.com"):
        return False, f"placeholder host blocked: {host}"
    return True, "ok"


def check_reachable(url: str, *, timeout: float = 12.0) -> tuple[bool, str, int | None]:
    ok, reason = format_ok(url)
    if not ok:
        return False, reason, None
    # Browser-like UA — many publishers block bare bots with 403.
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, headers=headers) as client:
            try:
                resp = client.head(url)
                # Some CDNs reject HEAD or challenge bots
                if resp.status_code in {405, 403, 401, 429} or resp.status_code >= 500:
                    resp = client.get(url)
            except httpx.HTTPError:
                resp = client.get(url)
        code = resp.status_code
        if 200 <= code < 400:
            return True, f"reachable ({code})", code
        # Publisher bot-walls still imply the URL is real enough for Shorts citations.
        if code in {401, 403, 429}:
            return True, f"reachable_soft ({code} bot-wall)", code
        return False, f"bad status {code}", code
    except httpx.HTTPError as exc:
        return False, f"request failed: {exc.__class__.__name__}", None


def verify_sources(
    sources: list[str],
    *,
    min_count: int = 1,
    check_http: bool = True,
    require_all: bool = False,
) -> tuple[bool, list[dict]]:
    """Return (ok, report_rows).

    ok requires at least min_count passing rows. When require_all is True,
    every listed URL must also pass (a single good URL cannot mask a 404).
    """
    rows: list[dict] = []
    cleaned = [_normalize(s) for s in sources if _normalize(s)]
    if len(cleaned) < min_count:
        rows.append(
            {
                "url": None,
                "ok": False,
                "reason": f"need at least {min_count} source URL(s), got {len(cleaned)}",
            }
        )
        return False, rows

    pass_count = 0
    for url in cleaned:
        if not check_http:
            ok, reason = format_ok(url)
            rows.append({"url": url, "ok": ok, "reason": reason, "status": None})
            if ok:
                pass_count += 1
            continue
        ok, reason, status = check_reachable(url)
        rows.append({"url": url, "ok": ok, "reason": reason, "status": status})
        if ok:
            pass_count += 1
        else:
            logger.warning("Source failed: %s (%s)", url, reason)

    if require_all and any(not r.get("ok") for r in rows if r.get("url") is not None):
        return False, rows
    return pass_count >= min_count, rows


def verify_sources_gate(
    sources: list[str],
    *,
    episode_dir: Path | None = None,
    min_count: int = 1,
    check_http: bool = True,
    require_all: bool = False,
) -> tuple[bool, list[str]]:
    ok, rows = verify_sources(
        sources,
        min_count=min_count,
        check_http=check_http,
        require_all=require_all,
    )
    if episode_dir is not None:
        episode_dir.mkdir(parents=True, exist_ok=True)
        (episode_dir / "sources_report.json").write_text(
            json.dumps({"ok": ok, "sources": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    errors = [f"{r.get('url') or '-'}: {r['reason']}" for r in rows if not r.get("ok")]
    return ok, errors
