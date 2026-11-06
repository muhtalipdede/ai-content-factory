"""End-to-end episode orchestrator with pause and cost gates."""

from __future__ import annotations

import logging
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

import config
from pipeline import state
from pipeline.audio import generate_audio
from pipeline.assets import generate_assets
from pipeline.checkpoint import load_valid_assets, load_valid_audio, load_valid_final
from pipeline.claims import verify_claims_gate
from pipeline.compose import compose_episode
from pipeline.ideate import generate_idea
from pipeline.local_scripts import (
    load_script_file,
    mark_done,
    next_queued_script,
    write_template_if_missing,
)
from pipeline.models import EpisodeScript
from pipeline.publish import publish_episode
from pipeline.script import generate_script, save_script
from pipeline.sources import verify_sources_gate
from pipeline.thumbnail import build_metadata, create_thumbnail

console = Console()
logging.basicConfig(
    level=logging.INFO,
    format="%(message)s",
    handlers=[RichHandler(console=console, rich_tracebacks=True)],
)
logger = logging.getLogger("aigen")


def episode_dir_for(episode_id: str) -> Path:
    path = config.EPISODES_DIR / episode_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_script(
    *,
    script_path: str | Path | None = None,
    idea: dict | None = None,
    dry_run: bool = False,
) -> tuple[EpisodeScript, Path | None, dict]:
    """Load local script (default) or generate via LLM if SCRIPT_SOURCE=openai.

    Returns (script, source_path_or_none, idea_meta) where idea_meta may hold
    title_a / title_b for Studio A/B testing.
    """
    write_template_if_missing()

    if script_path:
        path = Path(script_path)
        if not path.is_file():
            raise FileNotFoundError(f"Script not found: {path}")
        return load_script_file(path), path, {}

    use_local = config.SCRIPT_SOURCE.lower() == "local"
    if use_local:
        queued = next_queued_script()
        if queued:
            script, path = queued
            logger.info("Using queued script: %s", path.name)
            return script, path, {}
        raise SystemExit(
            "SCRIPT_SOURCE=local but queue is empty.\n"
            f"Add a JSON file under {config.SCRIPTS_QUEUE_DIR}\n"
            f"(template: {config.SCRIPTS_DIR / 'templates' / 'episode.template.json'})\n"
            "Or pass --script path/to/script.json"
        )

    idea = idea or generate_idea(dry_run=dry_run)
    logger.info("Idea (LLM): %s", idea["title"])
    idea_meta = {
        "claim": idea.get("claim") or "",
        "title_a": idea.get("title_a") or "",
        "title_b": idea.get("title_b") or "",
        "preferred_title": idea.get("preferred_title") or idea.get("title") or "",
        "hook": idea.get("hook") or "",
        "source_hint": idea.get("source_hint") or "",
    }
    return generate_script(idea, dry_run=dry_run), None, idea_meta


def run_episode(
    *,
    dry_run: bool = False,
    skip_publish: bool = False,
    idea: dict | None = None,
    script_path: str | Path | None = None,
    reuse_assets: bool = False,
) -> dict:
    if not config.PROJECT_ID:
        raise RuntimeError("No project selected. Use --project or PROJECT_ID.")
    config.ensure_dirs()

    # Dry-run should not consume daily publish caps; still honor PAUSE.
    if state.is_paused():
        logger.error("Blocked: paused (%s)", config.PAUSE_FLAG)
        raise SystemExit(1)
    if not dry_run:
        ok, reason = state.can_produce()
        if not ok:
            logger.error("Blocked: %s", reason)
            raise SystemExit(1)

        estimated = state.estimate_episode_cost(
            num_shots=6, ai_video_shots=config.MAX_AI_VIDEO_SHOTS
        )
        budget_ok, budget_reason = state.within_budget(estimated)
        if not budget_ok:
            logger.error("Blocked: %s", budget_reason)
            raise SystemExit(1)

    display = config.PROJECT.get("display_name") or config.PROJECT_ID
    console.rule(f"[bold cyan]{display} — episode run [{config.PROJECT_ID}]")
    script, source_script, idea_meta = resolve_script(
        script_path=script_path, idea=idea, dry_run=dry_run
    )
    logger.info("Script: %s", script.title)

    ep_dir = episode_dir_for(script.episode_id)

    # If resuming with valid audio, restore prior shot timings (TTS-aligned).
    prior_script_path = ep_dir / "script.json"
    if (
        not dry_run
        and prior_script_path.is_file()
        and load_valid_audio(ep_dir, script, log=False)
    ):
        try:
            prior = EpisodeScript.model_validate_json(
                prior_script_path.read_text(encoding="utf-8")
            )
            if (
                prior.episode_id == script.episode_id
                and len(prior.shots) == len(script.shots)
            ):
                for cur, old in zip(script.shots, prior.shots):
                    cur.duration_sec = old.duration_sec
                script.recompute_duration()
                logger.info("Checkpoint: restored shot timings from prior script.json")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not restore prior script timings: %s", exc)

    save_script(script, ep_dir)
    if idea_meta:
        import json

        (ep_dir / "titles.json").write_text(
            json.dumps(idea_meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    logger.info(
        "Script ready: %d shots, %.1fs — %s",
        len(script.shots),
        script.total_duration_sec,
        ep_dir,
    )

    # Fail before paid image/TTS spend when sources are required.
    if config.REQUIRES_SOURCES and not dry_run:
        src_ok, src_errors = verify_sources_gate(
            script.sources,
            episode_dir=ep_dir,
            min_count=config.MIN_SOURCES,
            check_http=True,
            require_all=True,
        )
        if not src_ok:
            logger.warning(
                "Source gate failed; attempting one source repair pass:\n%s",
                "\n".join(src_errors),
            )
            try:
                from pipeline.script import repair_script_sources

                script = repair_script_sources(script, src_errors)
                save_script(script, ep_dir)
                src_ok, src_errors = verify_sources_gate(
                    script.sources,
                    episode_dir=ep_dir,
                    min_count=config.MIN_SOURCES,
                    check_http=True,
                    require_all=True,
                )
            except Exception as exc:  # noqa: BLE001
                logger.error("Source repair failed: %s", exc)
                src_ok = False
        if not src_ok:
            logger.error("Blocked by source gate:\n%s", "\n".join(src_errors))
            raise SystemExit(1)
        logger.info("Source gate passed (%d URL(s))", len(script.sources))

    if config.VERIFY_CLAIMS and not dry_run:
        claim_ok, claim_errors = verify_claims_gate(
            script,
            idea_claim=str(idea_meta.get("claim") or ""),
            episode_dir=ep_dir,
            fetch=True,
        )
        if not claim_ok:
            logger.error("Blocked by claim evidence gate:\n%s", "\n".join(claim_errors))
            raise SystemExit(1)
        logger.info("Claim evidence gate passed")

    # Stage checkpoints: auto-reuse valid artifacts (idempotent resume).
    # reuse_assets kept for CLI compatibility; behavior is now default-on.
    _ = reuse_assets
    assets = None if dry_run else load_valid_assets(ep_dir, script)
    if assets is None:
        assets = generate_assets(script, ep_dir, dry_run=dry_run)

    audio = None if dry_run else load_valid_audio(ep_dir, script)
    if audio is None:
        audio = generate_audio(script, ep_dir, dry_run=dry_run)
    save_script(script, ep_dir)

    video_path = None if dry_run else load_valid_final(ep_dir)
    if video_path is None:
        video_path = compose_episode(script, ep_dir, assets, audio)
    logger.info("Composed: %s", video_path)

    first_shot = script.shots[0].shot_id
    hero = Path(assets[first_shot]["image"])  # type: ignore[arg-type]
    thumb = create_thumbnail(script, ep_dir, hero_image=hero)
    meta = build_metadata(
        script,
        ep_dir,
        video_path,
        thumb,
        title_a=str(idea_meta.get("title_a") or ""),
        title_b=str(idea_meta.get("title_b") or ""),
    )

    if skip_publish:
        dry_run = True
    elif config.REQUIRES_SOURCES and not dry_run:
        # Re-check right before upload (report already on disk).
        src_ok, src_errors = verify_sources_gate(
            script.sources,
            episode_dir=ep_dir,
            min_count=config.MIN_SOURCES,
            check_http=True,
            require_all=True,
        )
        if not src_ok:
            logger.error("Publish blocked by source gate:\n%s", "\n".join(src_errors))
            raise SystemExit(1)
        if config.VERIFY_CLAIMS:
            claim_ok, claim_errors = verify_claims_gate(
                script,
                idea_claim=str(idea_meta.get("claim") or ""),
                episode_dir=ep_dir,
                fetch=True,
            )
            if not claim_ok:
                logger.error(
                    "Publish blocked by claim evidence gate:\n%s",
                    "\n".join(claim_errors),
                )
                raise SystemExit(1)
    meta = publish_episode(meta, ep_dir, dry_run=dry_run)

    # Only retire queue scripts on real (non-dry) runs so tests don't drain the queue.
    if (
        not dry_run
        and source_script
        and source_script.parent.resolve() == config.SCRIPTS_QUEUE_DIR.resolve()
    ):
        done = mark_done(source_script, script.episode_id)
        logger.info("Moved script to done: %s", done.name)

    if not dry_run:
        claim = str(idea_meta.get("claim") or script.summary or "")
        history_entry = {
            "project_id": config.PROJECT_ID,
            "episode_id": script.episode_id,
            "title": script.title,
            "normalized_title": state.normalize_topic_key(script.title),
            "claim": claim,
            "normalized_claim": state.normalize_topic_key(claim),
            "summary": script.summary,
            "lesson": script.lesson,
            "youtube_video_id": meta.youtube_video_id,
            "path": str(ep_dir),
            "script_source": str(source_script) if source_script else "openai",
            "dry_run": False,
        }
        state.append_history(history_entry)
        try:
            from pipeline import memory as learning_memory

            learning_memory.upsert_episode(history_entry)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Learning memory upsert failed: %s", exc)
        state.record_cost(0.0, episode=True)

    result = {
        "project_id": config.PROJECT_ID,
        "episode_id": script.episode_id,
        "title": script.title,
        "dir": str(ep_dir),
        "video": str(video_path),
        "thumbnail": str(thumb),
        "youtube_video_id": meta.youtube_video_id,
    }
    console.print(result)
    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run one AI-generated episode")
    parser.add_argument("--project", "-p", default=None, help="Project id under projects/")
    parser.add_argument("--dry-run", action="store_true", help="No paid APIs / no upload")
    parser.add_argument("--skip-publish", action="store_true", help="Produce but do not upload")
    parser.add_argument("--script", type=str, default=None, help="Path to local script JSON")
    args = parser.parse_args()
    if args.project:
        config.set_project(args.project)
        from pipeline.brand import clear_caches

        clear_caches()
    run_episode(
        dry_run=args.dry_run,
        skip_publish=args.skip_publish,
        script_path=args.script,
    )
