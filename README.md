# AI Content Factory

Multi-project YouTube Shorts pipeline: **ideate → research gates → script → visuals/TTS → FFmpeg → thumbnail → YouTube**.

Each channel is an isolated folder under `projects/<id>/` (brand, prompts, queue, credentials, episode outputs). The shared motor lives in `pipeline/`.

## Features

- Multi-tenant projects via `project.json`
- Source URL gates and optional claim verification
- Narrator-only or character cast modes
- Dry-run / skip-publish safety switches
- Resume-friendly episode checkpoints
- Learning memory hooks for analytics feedback

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY for live generation
```

Requires `ffmpeg` / `ffprobe` on your PATH.

```bash
python jobs/run_episode.py projects
python jobs/run_episode.py -p demo_facts scripts --validate
python jobs/run_episode.py -p demo_facts run \
  --script projects/demo_facts/data/scripts/queue/ep_demo_postit.json \
  --dry-run
```

More detail: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

## Layout

| Path | Role |
|------|------|
| `pipeline/` | Shared production motor |
| `orchestrator.py` | End-to-end episode run |
| `jobs/run_episode.py` | CLI (`-p` / `--project`) |
| `projects/_template/` | Copy-me starter |
| `projects/demo_facts/` | Faceless educational demo |
| `credentials/` | Shared Google OAuth Desktop client (gitignored) |
| `tests/` | Offline unit tests |

## New project

```bash
cp -R projects/_template projects/my_channel
# edit project.json + brand/*
python jobs/run_episode.py -p my_channel run --dry-run
```

See [docs/NEW_PROJECT.md](docs/NEW_PROJECT.md) and [docs/YOUTUBE_OAUTH.md](docs/YOUTUBE_OAUTH.md).

## Environment

Copy `.env.example` → `.env`. Important knobs:

- `PROJECT_ID` — default project (`demo_facts`)
- `OPENAI_API_KEY` — required for LLM / image / TTS providers
- `AUTO_PUBLISH` / `DRY_RUN` — publish safety
- `DAILY_MAX_EPISODES` / `DAILY_MAX_COST_USD` — soft caps

`made_for_kids`, source gates, duration windows, and prompts come from each project's `project.json`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## License

MIT — see [LICENSE](LICENSE).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).
