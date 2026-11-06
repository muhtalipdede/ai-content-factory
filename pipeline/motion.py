"""Deterministic cinematic still-motion profiles for Shorts (no AI video)."""

from __future__ import annotations

from pipeline.models import Shot

# Alternating profiles reduce the "same Ken Burns every shot" slideshow feel.
_PROFILES_STILL = (
    "zoom_in_slow",
    "drift_up",
    "zoom_out",
    "pan_right",
    "zoom_in",
    "pan_left",
)
_PROFILES_PAN = (
    "pan_right",
    "pan_left",
    "drift_up",
    "zoom_in",
)


def select_motion_profile(shot: Shot, shot_index: int = 0) -> str:
    if shot.motion == "ai_video":
        return "hold"
    if shot.motion == "pan":
        return _PROFILES_PAN[shot_index % len(_PROFILES_PAN)]
    return _PROFILES_STILL[shot_index % len(_PROFILES_STILL)]


def ken_burns_filter(
    shot: Shot,
    width: int,
    height: int,
    duration: float,
    *,
    shot_index: int = 0,
    profile: str | None = None,
) -> str:
    """Return an ffmpeg vf chain: scale + zoompan + yuv420p."""
    frames = max(int(duration * 30), 1)
    n = max(frames - 1, 1)
    profile = profile or select_motion_profile(shot, shot_index)

    # Pre-scale so pans have headroom.
    scale = f"scale={width * 2}:{height * 2}"

    if profile == "hold":
        zpan = (
            f"zoompan=z='1':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    elif profile == "zoom_out":
        zpan = (
            f"zoompan=z='if(eq(on,1),1.16,max(zoom-0.0011,1.0))'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    elif profile == "pan_right":
        zpan = (
            f"zoompan=z='1.14'"
            f":x='(iw-iw/zoom)*on/{n}'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    elif profile == "pan_left":
        zpan = (
            f"zoompan=z='1.14'"
            f":x='(iw-iw/zoom)*(1-on/{n})'"
            f":y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    elif profile == "drift_up":
        zpan = (
            f"zoompan=z='min(1.05+0.0007*on,1.14)'"
            f":x='iw/2-(iw/zoom/2)'"
            f":y='(ih-ih/zoom)*(1-on/{n})'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    elif profile == "zoom_in_slow":
        zpan = (
            f"zoompan=z='min(zoom+0.0007,1.10)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )
    else:  # zoom_in
        zpan = (
            f"zoompan=z='min(zoom+0.0012,1.16)'"
            f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)'"
            f":d={frames}:s={width}x{height}:fps=30"
        )

    # Soft documentary grade — mild contrast, teal-amber friendly, no heavy look.
    grade = "eq=contrast=1.04:saturation=0.92:brightness=0.01"
    return f"{scale},{zpan},format=yuv420p,{grade}"
