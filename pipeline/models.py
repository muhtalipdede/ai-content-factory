"""Shared Pydantic models for episode scripts and metadata."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


Motion = Literal["still", "pan", "ai_video"]


class Shot(BaseModel):
    shot_id: str
    duration_sec: float = Field(ge=1.0, le=20.0)
    # Validated against brand/characters.json (+ narrator) at load time
    speaker: str = "narrator"
    dialogue: str
    visual_prompt: str
    characters: list[str] = Field(default_factory=list)
    motion: Motion = "still"
    sfx: str | None = None


class EpisodeScript(BaseModel):
    episode_id: str
    title: str
    summary: str
    lesson: str = ""
    tags: list[str] = Field(default_factory=list)
    description: str = ""
    sources: list[str] = Field(default_factory=list)
    shots: list[Shot]
    total_duration_sec: float = 0.0

    def recompute_duration(self) -> None:
        self.total_duration_sec = round(sum(s.duration_sec for s in self.shots), 2)


class EpisodeMeta(BaseModel):
    episode_id: str
    title: str
    description: str
    tags: list[str]
    made_for_kids: bool = True
    privacy_status: str = "public"
    category_id: str = "1"
    thumbnail_path: str | None = None
    video_path: str | None = None
    youtube_video_id: str | None = None
