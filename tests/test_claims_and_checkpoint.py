"""Unit tests for Cycle 2 claim gate + checkpoints (no network)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config
from pipeline.checkpoint import load_valid_assets, load_valid_audio, load_valid_final
from pipeline.claims import (
    extract_claims,
    html_to_text,
    score_claim_against_text,
    verify_claims_gate,
)
from pipeline.models import EpisodeScript, Shot


def _script() -> EpisodeScript:
    shots = [
        Shot(
            shot_id="s01",
            duration_sec=8,
            speaker="narrator",
            dialogue="Who invented the digital camera?",
            visual_prompt="archive still",
            characters=[],
            motion="still",
        ),
        Shot(
            shot_id="s02",
            duration_sec=10,
            speaker="narrator",
            dialogue="Kodak invented digital photography in 1975 but did not put it on shelves.",
            visual_prompt="lab still",
            characters=[],
            motion="still",
        ),
    ]
    script = EpisodeScript(
        episode_id="ep_claim_test",
        title="Few people know: Kodak invented digital photography in 1975",
        summary="Kodak invented an early digital camera in 1975.",
        lesson="Shipping on time matters.",
        sources=[
            "https://en.wikipedia.org/wiki/Kodak",
            "https://www.britannica.com/topic/Eastman-Kodak-Company",
        ],
        shots=shots,
    )
    script.recompute_duration()
    return script


class TestClaims(unittest.TestCase):
    def test_html_to_text_strips_tags(self) -> None:
        text = html_to_text("<html><script>x</script><p>Kodak invented digital in 1975</p>")
        self.assertIn("Kodak invented digital in 1975", text)
        self.assertNotIn("<p>", text)

    def test_extract_claims(self) -> None:
        claims = extract_claims(
            _script(),
            idea_claim="Kodak delayed commercialization of digital cameras",
        )
        self.assertTrue(claims)
        fields = {c["source_field"] for c in claims}
        self.assertIn("title", fields)
        self.assertIn("idea_claim", fields)

    def test_score_supported_overlap(self) -> None:
        page = (
            "Eastman Kodak Company invented an early digital camera in 1975. "
            "The company delayed commercialization to protect film sales. "
            "Kodak invented digital photography in 1975."
        )
        scored = score_claim_against_text(
            "Kodak invented digital photography in 1975",
            page,
        )
        self.assertEqual(scored["status"], "supported")
        self.assertTrue(scored["passage"])

    def test_score_unsupported(self) -> None:
        scored = score_claim_against_text(
            "Kodak invented digital photography in 1975",
            "Recipes for chocolate cake and gardening tips for spring.",
        )
        self.assertEqual(scored["status"], "unsupported")

    def test_verify_claims_gate_with_injected_pages(self) -> None:
        config.set_project("demo_facts")
        self.assertTrue(config.VERIFY_CLAIMS)
        script = _script()
        page = (
            "Kodak invented the digital camera in 1975 but did not put it on shelves. "
            "Kodak invented digital photography in 1975 and delayed commercialization."
        )
        pages = {
            script.sources[0]: page,
            script.sources[1]: page,
        }
        ok, errors = verify_claims_gate(
            script,
            idea_claim="Kodak invented digital photography in 1975",
            episode_dir=None,
            fetch=False,
            page_texts=pages,
        )
        self.assertTrue(ok, errors)


class TestCheckpoint(unittest.TestCase):
    def test_load_valid_assets_and_audio(self) -> None:
        script = _script()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            images = ep / "images"
            audio = ep / "audio"
            images.mkdir()
            audio.mkdir()
            assets = {}
            for shot in script.shots:
                img = images / f"{shot.shot_id}.png"
                img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
                assets[shot.shot_id] = {"image": str(img), "clip": None}
            (ep / "assets.json").write_text(json.dumps(assets), encoding="utf-8")

            narr = audio / "narration.mp3"
            narr.write_bytes(b"ID3" + b"\x00" * 200)
            audio_manifest = {
                "_narration": str(narr),
                "_mode": "continuous",
            }
            for shot in script.shots:
                p = audio / f"{shot.shot_id}.mp3"
                p.write_bytes(b"ID3" + b"\x00" * 50)
                audio_manifest[shot.shot_id] = str(p)
            (ep / "audio.json").write_text(json.dumps(audio_manifest), encoding="utf-8")

            final = ep / "final.mp4"
            final.write_bytes(b"\x00" * 12_000)

            self.assertIsNotNone(load_valid_assets(ep, script))
            self.assertIsNotNone(load_valid_audio(ep, script))
            self.assertEqual(load_valid_final(ep), final)

    def test_missing_image_rejects_assets(self) -> None:
        script = _script()
        with tempfile.TemporaryDirectory() as tmp:
            ep = Path(tmp)
            (ep / "assets.json").write_text(
                json.dumps(
                    {
                        "s01": {"image": str(ep / "missing.png"), "clip": None},
                        "s02": {"image": str(ep / "missing2.png"), "clip": None},
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(load_valid_assets(ep, script))


if __name__ == "__main__":
    unittest.main()
