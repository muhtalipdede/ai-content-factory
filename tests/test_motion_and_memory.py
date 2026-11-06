"""Unit tests for Cycle 3 motion profiles + learning memory."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from pipeline.models import Shot
from pipeline.motion import ken_burns_filter, select_motion_profile


def _shot(motion: str = "still") -> Shot:
    return Shot(
        shot_id="s01",
        duration_sec=5,
        speaker="narrator",
        dialogue="Test",
        visual_prompt="still",
        characters=[],
        motion=motion,  # type: ignore[arg-type]
    )


class TestMotion(unittest.TestCase):
    def test_profiles_alternate(self) -> None:
        a = select_motion_profile(_shot("still"), 0)
        b = select_motion_profile(_shot("still"), 1)
        self.assertNotEqual(a, b)

    def test_pan_uses_pan_family(self) -> None:
        profile = select_motion_profile(_shot("pan"), 0)
        self.assertIn(profile, {"pan_right", "pan_left", "drift_up", "zoom_in"})

    def test_filter_contains_zoompan_and_grade(self) -> None:
        vf = ken_burns_filter(_shot("still"), 1080, 1920, 4.0, shot_index=0)
        self.assertIn("zoompan=", vf)
        self.assertIn("format=yuv420p", vf)
        self.assertIn("eq=", vf)

    def test_zoom_out_profile(self) -> None:
        vf = ken_burns_filter(
            _shot("still"), 1080, 1920, 3.0, shot_index=0, profile="zoom_out"
        )
        self.assertIn("max(zoom-0.0011,1.0)", vf)


class TestMemory(unittest.TestCase):
    def test_upsert_and_hints(self) -> None:
        config.set_project("demo_facts")
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            with mock.patch.object(config, "STATE_DIR", state_dir):
                from pipeline import memory

                memory.upsert_episode(
                    {
                        "episode_id": "ep_a",
                        "title": "Kodak shelved digital cameras",
                        "claim": "Kodak invented digital photography in 1975",
                        "youtube_video_id": "abc123",
                    }
                )
                memory.add_snapshot(
                    "ep_a",
                    {
                        "ok": True,
                        "metrics": {"viewCount": 1200, "likeCount": 40},
                    },
                )
                memory.upsert_episode(
                    {
                        "episode_id": "ep_b",
                        "title": "Other topic",
                        "claim": "another claim",
                        "youtube_video_id": "def456",
                    }
                )
                memory.add_snapshot(
                    "ep_b",
                    {"ok": True, "metrics": {"viewCount": 90, "likeCount": 2}},
                )
                top = memory.top_performing(limit=1)
                self.assertEqual(top[0]["episode_id"], "ep_a")
                hints = memory.performance_hints(limit=2)
                self.assertEqual(hints["sample_size"], 2)
                self.assertIn("Kodak", hints["top_titles"][0])


if __name__ == "__main__":
    unittest.main()
