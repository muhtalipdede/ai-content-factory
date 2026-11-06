"""Central configuration loaded from environment + per-project project.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

PROJECTS_DIR = ROOT / "projects"
# Shared OAuth client (tokens are per-project)
CREDENTIALS_DIR = ROOT / "credentials"
YOUTUBE_CLIENT_SECRETS = CREDENTIALS_DIR / "client_secret.json"

# API keys (shared)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
OPENAI_IMAGE_MODEL = os.getenv("OPENAI_IMAGE_MODEL", "gpt-image-1.5")
OPENAI_TTS_MODEL = os.getenv("OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
FAL_KEY = os.getenv("FAL_KEY", "")
REPLICATE_API_TOKEN = os.getenv("REPLICATE_API_TOKEN", "")
RUNWAY_API_KEY = os.getenv("RUNWAY_API_KEY", "")
KLING_API_KEY = os.getenv("KLING_API_KEY", "")

# Providers (shared defaults; projects may override some via project.json)
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "openai")
BRAND_REF_PROVIDER = os.getenv("BRAND_REF_PROVIDER", "auto")
TTS_PROVIDER = os.getenv("TTS_PROVIDER", "openai")  # openai | elevenlabs
VIDEO_PROVIDER = os.getenv("VIDEO_PROVIDER", "stub")  # stub | runway | kling | fal

# Episode geometry defaults
VIDEO_FORMAT = os.getenv("VIDEO_FORMAT", "shorts")  # shorts | landscape
SHORTS_WIDTH = int(os.getenv("SHORTS_WIDTH", "1080"))
SHORTS_HEIGHT = int(os.getenv("SHORTS_HEIGHT", "1920"))
LANDSCAPE_WIDTH = int(os.getenv("LANDSCAPE_WIDTH", "1920"))
LANDSCAPE_HEIGHT = int(os.getenv("LANDSCAPE_HEIGHT", "1080"))
TARGET_DURATION_SEC = float(os.getenv("TARGET_DURATION_SEC", "60"))
MIN_DURATION_SEC = float(os.getenv("MIN_DURATION_SEC", "45"))
MAX_DURATION_SEC = float(os.getenv("MAX_DURATION_SEC", "90"))
AI_VIDEO_MAX_SEC = float(os.getenv("AI_VIDEO_MAX_SEC", "4"))

# Publishing / run flags (env can still override after project load)
AUTO_PUBLISH = os.getenv("AUTO_PUBLISH", "true").lower() in {"1", "true", "yes"}
DRY_RUN = os.getenv("DRY_RUN", "false").lower() in {"1", "true", "yes"}
PUBLISH_AT = os.getenv("PUBLISH_AT", "")
YOUTUBE_PRIVACY = os.getenv("YOUTUBE_PRIVACY", "")  # empty → project.json

# Cost / rate limits
DAILY_MAX_EPISODES = int(os.getenv("DAILY_MAX_EPISODES", "2"))
DAILY_MAX_COST_USD = float(os.getenv("DAILY_MAX_COST_USD", "15"))
EST_COST_SCRIPT = float(os.getenv("EST_COST_SCRIPT", "0.05"))
EST_COST_IMAGE = float(os.getenv("EST_COST_IMAGE", "0.04"))
EST_COST_TTS_PER_SHOT = float(os.getenv("EST_COST_TTS_PER_SHOT", "0.02"))
EST_COST_AI_VIDEO = float(os.getenv("EST_COST_AI_VIDEO", "0.50"))

# FFmpeg (prefer ffmpeg-full if present — includes libass for karaoke captions)
def _default_ffmpeg() -> str:
    candidates = [
        "/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg",
        "/usr/local/opt/ffmpeg-full/bin/ffmpeg",
        "ffmpeg",
    ]
    for c in candidates:
        if c == "ffmpeg" or Path(c).is_file():
            return c
    return "ffmpeg"


def _default_ffprobe() -> str:
    ffmpeg = os.getenv("FFMPEG_BIN") or _default_ffmpeg()
    if ffmpeg.endswith("/ffmpeg"):
        probe = ffmpeg[: -len("ffmpeg")] + "ffprobe"
        if Path(probe).is_file():
            return probe
    return "ffprobe"


FFMPEG_BIN = os.getenv("FFMPEG_BIN") or _default_ffmpeg()
FFPROBE_BIN = os.getenv("FFPROBE_BIN") or _default_ffprobe()

# --- Project-scoped state (mutated by set_project) ---
PROJECT_ID: str = ""
PROJECT: dict[str, Any] = {}
PROJECT_ROOT: Path = ROOT
BRAND_DIR: Path = ROOT
DATA_DIR: Path = ROOT
EPISODES_DIR: Path = ROOT
STATE_DIR: Path = ROOT
SCRIPTS_DIR: Path = ROOT
SCRIPTS_QUEUE_DIR: Path = ROOT
SCRIPTS_DONE_DIR: Path = ROOT
HISTORY_PATH: Path = ROOT
COST_PATH: Path = ROOT
PAUSE_FLAG: Path = ROOT
ASSETS_DIR: Path = ROOT
MUSIC_DIR: Path = ROOT
YOUTUBE_TOKEN_PATH: Path = ROOT

LANGUAGE: str = os.getenv("LANGUAGE", "tr")
SCRIPT_SOURCE: str = os.getenv("SCRIPT_SOURCE", "")
IMAGE_USE_REFS: bool = os.getenv("IMAGE_USE_REFS", "").lower() in {"1", "true", "yes"}
_IMAGE_USE_REFS_ENV = os.getenv("IMAGE_USE_REFS")
_SCRIPT_SOURCE_ENV = os.getenv("SCRIPT_SOURCE")
_MADE_FOR_KIDS_ENV = os.getenv("MADE_FOR_KIDS")
_MAX_AI_VIDEO_SHOTS_ENV = os.getenv("MAX_AI_VIDEO_SHOTS")
_LANGUAGE_ENV = os.getenv("LANGUAGE")
_YOUTUBE_PRIVACY_ENV = os.getenv("YOUTUBE_PRIVACY")
_YOUTUBE_CATEGORY_ENV = os.getenv("YOUTUBE_CATEGORY_ID")

MADE_FOR_KIDS: bool = True
YOUTUBE_CATEGORY_ID: str = "1"
MAX_AI_VIDEO_SHOTS: int = 2
SPEAKERS_MODE: str = "characters"
REQUIRES_SOURCES: bool = False
VERIFY_CLAIMS: bool = False
MIN_SOURCES: int = 1
SCRIPT_MIN_DURATION_SEC: float = 20.0
SCRIPT_MAX_DURATION_SEC: float = 120.0
BURN_CAPTIONS: bool = False
TTS_SPEED: float = 1.0
DEFAULT_TAGS: list[str] = []
DESCRIPTION_FOOTER: str = ""
IDEATION_SYSTEM: str = ""
SCRIPT_SYSTEM: str = ""
FALLBACK_IDEA: dict[str, str] = {}


def list_projects() -> list[str]:
    if not PROJECTS_DIR.is_dir():
        return []
    return sorted(
        p.name
        for p in PROJECTS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith("_") and (p / "project.json").is_file()
    )


def _read_project_json(project_id: str) -> dict[str, Any]:
    path = PROJECTS_DIR / project_id / "project.json"
    if not path.is_file():
        available = ", ".join(list_projects()) or "(none)"
        raise FileNotFoundError(
            f"Unknown project '{project_id}'. Expected {path}. Available: {available}"
        )
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Invalid project.json for {project_id}")
    return data


def _apply_paths(project_id: str) -> None:
    global PROJECT_ID, PROJECT_ROOT, BRAND_DIR, DATA_DIR, EPISODES_DIR, STATE_DIR
    global SCRIPTS_DIR, SCRIPTS_QUEUE_DIR, SCRIPTS_DONE_DIR, HISTORY_PATH, COST_PATH
    global PAUSE_FLAG, ASSETS_DIR, MUSIC_DIR, YOUTUBE_TOKEN_PATH

    PROJECT_ID = project_id
    PROJECT_ROOT = PROJECTS_DIR / project_id
    BRAND_DIR = PROJECT_ROOT / "brand"
    DATA_DIR = PROJECT_ROOT / "data"
    EPISODES_DIR = DATA_DIR / "episodes"
    STATE_DIR = DATA_DIR / "state"
    SCRIPTS_DIR = DATA_DIR / "scripts"
    SCRIPTS_QUEUE_DIR = SCRIPTS_DIR / "queue"
    SCRIPTS_DONE_DIR = SCRIPTS_DIR / "done"
    HISTORY_PATH = STATE_DIR / "history.json"
    COST_PATH = STATE_DIR / "cost.json"
    PAUSE_FLAG = STATE_DIR / "PAUSE"
    ASSETS_DIR = PROJECT_ROOT / "assets"
    MUSIC_DIR = ASSETS_DIR / "music"
    YOUTUBE_TOKEN_PATH = PROJECT_ROOT / "credentials" / "youtube_token.json"


def _apply_project_settings(data: dict[str, Any]) -> None:
    global PROJECT, LANGUAGE, SCRIPT_SOURCE, IMAGE_USE_REFS, MADE_FOR_KIDS
    global YOUTUBE_CATEGORY_ID, YOUTUBE_PRIVACY, MAX_AI_VIDEO_SHOTS
    global SPEAKERS_MODE, REQUIRES_SOURCES, VERIFY_CLAIMS, MIN_SOURCES
    global SCRIPT_MIN_DURATION_SEC, SCRIPT_MAX_DURATION_SEC
    global BURN_CAPTIONS, TTS_SPEED
    global DEFAULT_TAGS, DESCRIPTION_FOOTER
    global IDEATION_SYSTEM, SCRIPT_SYSTEM, FALLBACK_IDEA

    PROJECT = data

    LANGUAGE = _LANGUAGE_ENV or str(data.get("language") or "tr")

    if _SCRIPT_SOURCE_ENV:
        SCRIPT_SOURCE = _SCRIPT_SOURCE_ENV
    else:
        SCRIPT_SOURCE = str(data.get("script_source") or "local")

    if _IMAGE_USE_REFS_ENV is not None and _IMAGE_USE_REFS_ENV != "":
        IMAGE_USE_REFS = _IMAGE_USE_REFS_ENV.lower() in {"1", "true", "yes"}
    else:
        IMAGE_USE_REFS = bool(data.get("image_use_refs", False))

    if _MADE_FOR_KIDS_ENV is not None and _MADE_FOR_KIDS_ENV != "":
        MADE_FOR_KIDS = _MADE_FOR_KIDS_ENV.lower() in {"1", "true", "yes"}
    else:
        MADE_FOR_KIDS = bool(data.get("made_for_kids", False))

    if _YOUTUBE_CATEGORY_ENV:
        YOUTUBE_CATEGORY_ID = _YOUTUBE_CATEGORY_ENV
    else:
        YOUTUBE_CATEGORY_ID = str(data.get("youtube_category_id") or "22")

    if _YOUTUBE_PRIVACY_ENV:
        YOUTUBE_PRIVACY = _YOUTUBE_PRIVACY_ENV
    else:
        YOUTUBE_PRIVACY = str(data.get("privacy_status") or "public")

    if _MAX_AI_VIDEO_SHOTS_ENV is not None and _MAX_AI_VIDEO_SHOTS_ENV != "":
        MAX_AI_VIDEO_SHOTS = int(_MAX_AI_VIDEO_SHOTS_ENV)
    else:
        MAX_AI_VIDEO_SHOTS = int(data.get("max_ai_video_shots", 2))

    SPEAKERS_MODE = str(data.get("speakers_mode") or "characters")
    REQUIRES_SOURCES = bool(data.get("requires_sources", False))
    # Default on for source-backed projects unless project.json sets false.
    if "verify_claims" in data:
        VERIFY_CLAIMS = bool(data.get("verify_claims"))
    else:
        VERIFY_CLAIMS = REQUIRES_SOURCES
    try:
        MIN_SOURCES = max(1, int(data.get("min_sources", 1)))
    except (TypeError, ValueError):
        MIN_SOURCES = 1

    default_min = 30.0 if MADE_FOR_KIDS else 20.0
    default_max = 120.0
    try:
        SCRIPT_MIN_DURATION_SEC = float(data.get("min_duration_sec", default_min))
    except (TypeError, ValueError):
        SCRIPT_MIN_DURATION_SEC = default_min
    try:
        SCRIPT_MAX_DURATION_SEC = float(data.get("max_duration_sec", default_max))
    except (TypeError, ValueError):
        SCRIPT_MAX_DURATION_SEC = default_max
    if SCRIPT_MAX_DURATION_SEC < SCRIPT_MIN_DURATION_SEC:
        SCRIPT_MAX_DURATION_SEC = SCRIPT_MIN_DURATION_SEC

    BURN_CAPTIONS = bool(data.get("burn_captions", False))
    try:
        TTS_SPEED = float(data.get("tts_speed", 1.0))
    except (TypeError, ValueError):
        TTS_SPEED = 1.0
    TTS_SPEED = max(0.75, min(TTS_SPEED, 1.35))
    DEFAULT_TAGS = list(data.get("default_tags") or [])
    DESCRIPTION_FOOTER = str(data.get("description_footer") or "")
    IDEATION_SYSTEM = str(data.get("ideation_system") or "")
    SCRIPT_SYSTEM = str(data.get("script_system") or "")
    FALLBACK_IDEA = dict(data.get("fallback_idea") or {})


def set_project(project_id: str) -> dict[str, Any]:
    """Activate a project: paths + settings. Caller should clear brand caches."""
    data = _read_project_json(project_id)
    _apply_paths(project_id)
    _apply_project_settings(data)
    return data


def frame_size() -> tuple[int, int]:
    if VIDEO_FORMAT == "landscape":
        return LANDSCAPE_WIDTH, LANDSCAPE_HEIGHT
    return SHORTS_WIDTH, SHORTS_HEIGHT


def ensure_dirs() -> None:
    if not PROJECT_ID:
        raise RuntimeError("No project selected. Call config.set_project(id) first.")
    for path in (
        EPISODES_DIR,
        STATE_DIR,
        SCRIPTS_QUEUE_DIR,
        SCRIPTS_DONE_DIR,
        SCRIPTS_DIR / "templates",
        MUSIC_DIR,
        PROJECT_ROOT / "credentials",
        DATA_DIR / "brand_refs",
        CREDENTIALS_DIR,
    ):
        path.mkdir(parents=True, exist_ok=True)


# Default project from env (demo_facts)
_default_project = os.getenv("PROJECT_ID", "demo_facts")
if (PROJECTS_DIR / _default_project / "project.json").is_file():
    set_project(_default_project)
