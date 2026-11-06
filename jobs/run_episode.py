#!/usr/bin/env python3
"""CLI entrypoint for scheduled / manual episode production."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from orchestrator import run_episode  # noqa: E402
from pipeline import state  # noqa: E402
from pipeline.brand import clear_caches  # noqa: E402
from pipeline.local_scripts import (  # noqa: E402
    list_queued,
    load_script_file,
    write_template_if_missing,
)
from pipeline.publish import authorize_youtube  # noqa: E402


def _activate_project(project_id: str | None) -> None:
    pid = project_id or config.PROJECT_ID or "demo_facts"
    config.set_project(pid)
    clear_caches()


def main() -> None:
    parser = argparse.ArgumentParser(prog="run_episode")
    parser.add_argument(
        "--project",
        "-p",
        default=None,
        help=(
            "Project id under projects/ "
            f"(default: env PROJECT_ID or demo_facts). Available: {', '.join(config.list_projects())}"
        ),
    )
    sub = parser.add_subparsers(dest="cmd")

    run_p = sub.add_parser("run", help="Produce and optionally publish one episode")
    run_p.add_argument("--dry-run", action="store_true")
    run_p.add_argument("--skip-publish", action="store_true")
    run_p.add_argument(
        "--reuse-assets",
        action="store_true",
        help="Skip image generation; reuse episode images/ + assets.json",
    )
    run_p.add_argument(
        "--script",
        type=str,
        default=None,
        help="Local script JSON (skips queue / LLM)",
    )

    auth_p = sub.add_parser(
        "auth-youtube",
        help="One-time YouTube OAuth for active project (upload + readonly analytics)",
    )
    auth_p.add_argument(
        "--force",
        action="store_true",
        help="Delete existing token and force fresh consent",
    )
    auth_p.add_argument(
        "--console",
        action="store_true",
        help="Paste-code flow (use if browser shows 'something went wrong')",
    )
    sub.add_parser("pause", help="Create PAUSE flag (stop autonomous runs)")
    sub.add_parser("resume", help="Remove PAUSE flag")
    sub.add_parser("status", help="Show pause + daily cost stats")
    analytics_p = sub.add_parser(
        "analytics",
        help="Pull YouTube view/like snapshots into learning memory",
    )
    analytics_p.add_argument(
        "--limit",
        type=int,
        default=30,
        help="Max published episodes to refresh (default 30)",
    )
    sub.add_parser("projects", help="List available projects")

    list_p = sub.add_parser("scripts", help="List queued local scripts")
    list_p.add_argument("--validate", action="store_true", help="Validate each queued script")

    refs_p = sub.add_parser(
        "brand-refs",
        help="Generate character brand sheets into project data/brand_refs/",
    )
    refs_p.add_argument(
        "--character",
        action="append",
        dest="characters",
        help="Character id (repeatable). Default: missing only",
    )
    refs_p.add_argument(
        "--all",
        action="store_true",
        help="Generate for every character (skips existing unless --force)",
    )
    refs_p.add_argument("--force", action="store_true", help="Overwrite existing sheets")
    refs_p.add_argument(
        "--provider",
        choices=["auto", "openai", "fal", "pollinations", "local"],
        default="auto",
        help="Image backend (pollinations=free AI; local=offline fallback)",
    )

    args = parser.parse_args()

    if args.cmd == "projects":
        for pid in config.list_projects():
            marker = " *" if pid == (args.project or config.PROJECT_ID) else ""
            print(f"{pid}{marker}")
        return

    _activate_project(args.project)

    if args.cmd is None:
        run_episode()
        return

    if args.cmd == "run":
        run_episode(
            dry_run=args.dry_run,
            skip_publish=args.skip_publish,
            script_path=args.script,
            reuse_assets=args.reuse_assets,
        )
    elif args.cmd == "auth-youtube":
        from pipeline.publish import OAUTH_LOCAL_PORT

        if args.console:
            print("Console OAuth: terminalde çıkan URL'yi aç, onayla, kodu buraya yapıştır.")
        else:
            print(
                f"Browser OAuth: http://localhost:{OAUTH_LOCAL_PORT}/ "
                "(hata olursa: --console)"
            )
        authorize_youtube(force=bool(args.force), console=bool(args.console))
        print(f"YouTube auth complete for project={config.PROJECT_ID}")
        print(f"Token: {config.YOUTUBE_TOKEN_PATH}")
        try:
            import json

            scopes = json.loads(config.YOUTUBE_TOKEN_PATH.read_text(encoding="utf-8")).get(
                "scopes"
            )
            print(f"scopes={scopes}")
        except Exception:  # noqa: BLE001
            pass
    elif args.cmd == "pause":
        state.set_pause(True)
        print(f"Paused [{config.PROJECT_ID}]. Remove {config.PAUSE_FLAG} or run: resume")
    elif args.cmd == "resume":
        state.set_pause(False)
        print(f"Resumed [{config.PROJECT_ID}].")
    elif args.cmd == "status":
        from pipeline.brand import brand_refs_exist, load_characters
        from pipeline.brand_refs_gen import missing_character_ids

        ok, reason = state.can_produce()
        stats = state.today_stats()
        queued = list_queued()
        chars = load_characters().get("characters") or {}
        refs = brand_refs_exist()
        print(f"project={config.PROJECT_ID}")
        print(f"display_name={config.PROJECT.get('display_name')}")
        print(f"script_source={config.SCRIPT_SOURCE}")
        print(f"made_for_kids={config.MADE_FOR_KIDS}")
        print(f"speakers_mode={config.SPEAKERS_MODE}")
        print(f"requires_sources={config.REQUIRES_SOURCES}")
        print(f"queued_scripts={len(queued)}")
        print(f"brand_refs={len(refs)}/{len(chars)}")
        for p in refs:
            try:
                print(f"  - {p.relative_to(config.PROJECT_ROOT)}")
            except ValueError:
                print(f"  - {p}")
        missing = missing_character_ids()
        if missing:
            print(f"missing_refs={', '.join(missing)}")
        print(f"paused={state.is_paused()}")
        print(f"can_produce={ok} ({reason})")
        print(f"today_episodes={stats['episodes']}/{config.DAILY_MAX_EPISODES}")
        print(f"today_cost_usd=${stats['cost_usd']:.2f}/${config.DAILY_MAX_COST_USD:.2f}")
        try:
            from pipeline import memory as learning_memory

            hints = learning_memory.performance_hints(limit=3)
            print(f"memory_episodes={len(learning_memory.list_episodes())}")
            if hints.get("top_titles"):
                print(f"top_titles={hints['top_titles']}")
        except Exception as exc:  # noqa: BLE001
            print(f"memory=unavailable ({exc})")
    elif args.cmd == "analytics":
        from pipeline.analytics import snapshot_published_episodes

        report = snapshot_published_episodes(limit=max(1, int(args.limit)))
        print(f"project={config.PROJECT_ID}")
        print(f"checked={report['checked']} ok={report['ok']} failed={report['failed']}")
        for row in report.get("results") or []:
            metrics = row.get("metrics") or {}
            if row.get("ok"):
                print(
                    f"- {row.get('episode_id')}: views={metrics.get('viewCount')} "
                    f"likes={metrics.get('likeCount')}"
                )
            else:
                print(f"- {row.get('episode_id')}: FAIL {row.get('error')}")
        hints = report.get("hints") or {}
        if hints.get("top_titles"):
            print(f"top_titles={hints['top_titles']}")
        if report["checked"] and report["ok"] == 0:
            print(
                "hint: token may lack youtube.readonly — re-run: "
                f"python jobs/run_episode.py -p {config.PROJECT_ID} auth-youtube"
            )
    elif args.cmd == "scripts":
        write_template_if_missing()
        queued = list_queued()
        if not queued:
            print(f"Queue empty [{config.PROJECT_ID}]: {config.SCRIPTS_QUEUE_DIR}")
            print(f"Template: {config.SCRIPTS_DIR / 'templates' / 'episode.template.json'}")
            return
        for path in queued:
            line = f"- {path.name}"
            if args.validate:
                try:
                    script = load_script_file(path)
                    line += f"  OK  ({len(script.shots)} shots, {script.total_duration_sec}s)  {script.title}"
                except Exception as exc:  # noqa: BLE001
                    line += f"  FAIL  {exc}"
            else:
                try:
                    meta = json.loads(path.read_text(encoding="utf-8"))
                    line += f"  {meta.get('title', '')}"
                except Exception:  # noqa: BLE001
                    pass
            print(line)
    elif args.cmd == "brand-refs":
        import logging

        from rich.logging import RichHandler

        from pipeline.brand_refs_gen import generate_all, missing_character_ids, resolve_provider

        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            handlers=[RichHandler(rich_tracebacks=True)],
        )
        provider = None if args.provider == "auto" else args.provider
        print(f"project={config.PROJECT_ID} provider={resolve_provider(provider)}")
        if not args.characters and not args.all and not missing_character_ids():
            print("All brand refs already exist. Use --force or --character id.")
            return
        results = generate_all(
            only_missing=not args.all and not args.characters,
            force=args.force,
            character_ids=args.characters,
            provider=provider,
        )
        for cid, status in results.items():
            print(f"{cid}: {status}")
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
