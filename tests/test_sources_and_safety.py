"""Unit tests for source/safety gates (no network)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from pipeline.models import EpisodeScript, Shot
from pipeline.safety import validate_script
from pipeline.sources import format_ok, verify_sources
from pipeline.state import normalize_topic_key, token_jaccard
from pipeline.titles import hook_is_question, title_quality_errors


def _shot(dialogue: str = "What happened?", duration: float = 10.0) -> Shot:
    return Shot(
        shot_id="s01",
        duration_sec=duration,
        speaker="narrator",
        dialogue=dialogue,
        visual_prompt="cinematic still of an archive room",
        characters=[],
        motion="still",
    )


class TestSourcesAndSafety(unittest.TestCase):
    def test_format_ok_blocks_placeholders(self) -> None:
        ok, reason = format_ok("https://example.com/foo")
        self.assertFalse(ok)
        self.assertTrue("placeholder" in reason or "blocked" in reason)

        ok, reason = format_ok("https://en.wikipedia.org/wiki/Kodak")
        self.assertTrue(ok)
        self.assertEqual(reason, "ok")

    def test_verify_sources_require_all_with_format_only(self) -> None:
        ok, rows = verify_sources(
            [
                "https://en.wikipedia.org/wiki/A",
                "https://example.com/bad",
            ],
            min_count=2,
            check_http=False,
            require_all=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any(not r["ok"] for r in rows))

        ok, rows = verify_sources(
            [
                "https://en.wikipedia.org/wiki/A",
                "https://britannica.com/topic/B",
            ],
            min_count=2,
            check_http=False,
            require_all=True,
        )
        self.assertTrue(ok)
        self.assertTrue(all(r["ok"] for r in rows if r.get("url")))

    def test_verify_sources_min_count(self) -> None:
        ok, rows = verify_sources(
            ["https://en.wikipedia.org/wiki/A"],
            min_count=2,
            check_http=False,
            require_all=True,
        )
        self.assertFalse(ok)
        self.assertTrue(any("need at least 2" in str(r.get("reason")) for r in rows))

    def test_demo_facts_duration_and_min_sources(self) -> None:
        config.set_project("demo_facts")
        self.assertEqual(config.MIN_SOURCES, 2)
        self.assertEqual(config.SCRIPT_MIN_DURATION_SEC, 40)
        self.assertEqual(config.SCRIPT_MAX_DURATION_SEC, 55)

        short = EpisodeScript(
            episode_id="ep_test_short",
            title="Few people know: Kodak shelved digital cameras for 18 years",
            summary="Kodak invented an early digital camera in 1975.",
            lesson="Timing matters.",
            sources=[
                "https://en.wikipedia.org/wiki/Kodak",
                "https://www.britannica.com/topic/Eastman-Kodak-Company",
            ],
            shots=[_shot(duration=8.0) for _ in range(3)],
        )
        short.recompute_duration()
        ok, errors = validate_script(short)
        self.assertFalse(ok)
        self.assertTrue(any("Too short" in e for e in errors))

        one_source = EpisodeScript(
            episode_id="ep_test_src",
            title="Few people know: Kodak shelved digital cameras for 18 years",
            summary="Kodak invented an early digital camera in 1975.",
            lesson="Timing matters.",
            sources=["https://en.wikipedia.org/wiki/Kodak"],
            shots=[_shot(duration=10.0) for _ in range(5)],
        )
        one_source.recompute_duration()
        ok, errors = validate_script(one_source)
        self.assertFalse(ok)
        self.assertTrue(any("at least 2 source" in e for e in errors))

    def test_template_defaults_are_looser(self) -> None:
        # _template is hidden from list_projects; set_project still loads it.
        config.set_project("_template")
        self.assertEqual(config.MIN_SOURCES, 1)
        self.assertEqual(config.SCRIPT_MIN_DURATION_SEC, 40)
        self.assertEqual(config.SCRIPT_MAX_DURATION_SEC, 60)
        self.assertFalse(config.REQUIRES_SOURCES)

    def test_hook_and_title_quality(self) -> None:
        self.assertTrue(hook_is_question("Who invented digital photography?"))
        self.assertFalse(hook_is_question("Who invented digital photography."))
        self.assertTrue(title_quality_errors("Huge scandal"))
        solid = title_quality_errors(
            "Few people know: Kodak shelved digital cameras for 18 years"
        )
        self.assertEqual(solid, [])

    def test_topic_jaccard_helpers(self) -> None:
        a = normalize_topic_key(
            "Few people know: Kodak shelved digital cameras for years"
        )
        b = normalize_topic_key("Kodak shelved digital cameras for years")
        self.assertIn("kodak", a)
        self.assertGreaterEqual(token_jaccard(a, b), 0.55)
        c = normalize_topic_key("Sagittarius A black hole shadow was imaged")
        self.assertLess(token_jaccard(a, c), 0.55)


if __name__ == "__main__":
    unittest.main()
