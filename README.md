# AI Content Factory

**Multi-project YouTube Shorts factory** — from idea to published video, with research gates, brand isolation, and cost-aware dry-runs.

Each channel lives in its own folder under `projects/<id>/`. The shared production motor lives in `pipeline/`.

```text
ideate → sources/claims → script → assets + TTS → FFmpeg → thumbnail → YouTube
```

## Why this exists

Most “AI Shorts” scripts are one-off notebooks. This repo is a reusable factory:

- **Multi-tenant** — one codebase, many channels (`project.json` + brand packs)
- **Safety-first** — source URL gates, optional claim verification, dry-run / pause flags
- **Resume-friendly** — episode checkpoints so a failed render doesn’t restart from zero
- **Provider-flexible** — OpenAI by default; optional FAL, Replicate, ElevenLabs, Runway/Kling stubs
- **Learning loop** — analytics snapshots feed back into memory for later ideation

## Quick start

**Requirements:** Python 3.11+, `ffmpeg` / `ffprobe` on your PATH.

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# set OPENAI_API_KEY for live generation
```

```bash
# list projects
python jobs/run_episode.py projects

# validate the demo queue
python jobs/run_episode.py -p demo_facts scripts --validate

# produce one episode without publishing
python jobs/run_episode.py -p demo_facts run \
  --script projects/demo_facts/data/scripts/queue/ep_demo_postit.json \
  --dry-run
```

Step-by-step walkthrough: [docs/GETTING_STARTED.md](docs/GETTING_STARTED.md).

## How an episode runs

| Stage | What happens |
|-------|----------------|
| **Resolve script** | Local queue JSON, or LLM ideation + script when `SCRIPT_SOURCE=openai` |
| **Gates** | Source URLs required (optional claim-vs-page overlap check) |
| **Assets** | Shot images / optional AI video clips + brand refs |
| **Audio** | TTS per segment, bed music, crossfades |
| **Compose** | FFmpeg timeline, captions, Shorts geometry |
| **Package** | Thumbnail + title/description/tags metadata |
| **Publish** | YouTube upload when `AUTO_PUBLISH` and not dry-run |

Soft caps (`DAILY_MAX_EPISODES`, `DAILY_MAX_COST_USD`) and a `PAUSE` flag stop runaway autonomous runs.

## CLI

```bash
python jobs/run_episode.py [-p PROJECT] <command>
```

| Command | Purpose |
|---------|---------|
| `run` | Produce (and optionally publish) one episode |
| `scripts [--validate]` | List / validate queued local scripts |
| `projects` | List project folders |
| `brand-refs` | Generate character brand sheets |
| `auth-youtube` | One-time OAuth for the active project |
| `analytics` | Pull view/like snapshots into learning memory |
| `status` / `pause` / `resume` | Cost stats and kill-switch |

Useful `run` flags: `--dry-run`, `--skip-publish`, `--script path.json`, `--reuse-assets`.

After `pip install -e .` you can also use the `acf` console script.

## Project layout

```text
ai-content-factory/
├── pipeline/           # shared motor (ideate, script, assets, TTS, compose, publish…)
├── orchestrator.py     # end-to-end episode runner
├── jobs/run_episode.py # CLI
├── config.py           # env + per-project paths
├── projects/
│   ├── _template/      # copy-me starter
│   └── demo_facts/     # faceless educational demo
├── credentials/        # shared Google OAuth Desktop client (gitignored)
├── docs/
└── tests/              # offline unit tests (no API keys)
```

| Path under `projects/<id>/` | Role |
|-----------------------------|------|
| `project.json` | Language, kids flag, gates, TTS, prompts, tags |
| `brand/` | Style, safety, characters |
| `data/scripts/queue/` | Local episode JSON waiting to run |
| `assets/music/` | Optional royalty-free bed track |
| `data/episodes/` | Outputs, checkpoints, published artifacts |

## New channel project

```bash
cp -R projects/_template projects/my_channel
# edit projects/my_channel/project.json and brand/*
python jobs/run_episode.py -p my_channel scripts --validate
python jobs/run_episode.py -p my_channel run --dry-run
```

Folders starting with `_` (like `_template`) are hidden from `projects`.

Full knobs: [docs/NEW_PROJECT.md](docs/NEW_PROJECT.md) · YouTube OAuth: [docs/YOUTUBE_OAUTH.md](docs/YOUTUBE_OAUTH.md).

## Environment

Copy `.env.example` → `.env`. Important knobs:

| Variable | Notes |
|----------|--------|
| `PROJECT_ID` | Default project (`demo_facts`) — CLI `-p` overrides |
| `OPENAI_API_KEY` | Required for live LLM / image / TTS |
| `AUTO_PUBLISH` / `DRY_RUN` | Publish safety (keep dry-run on until ready) |
| `IMAGE_PROVIDER` / `TTS_PROVIDER` / `VIDEO_PROVIDER` | `openai`, `fal`, `elevenlabs`, `stub`, … |
| `DAILY_MAX_EPISODES` / `DAILY_MAX_COST_USD` | Soft spend caps |

`made_for_kids`, source gates, duration windows, and system prompts come from each project’s `project.json`, not the global env.

## Demo: `demo_facts`

Bundled educational Shorts project — narrator-only, English, source + claim gates, burned captions. Sample queue script: Post-it / failed-glue fact under `projects/demo_facts/data/scripts/queue/`.

## Tests

```bash
python -m unittest discover -s tests -v
```

No network or API keys required.

## License & contributing

MIT — see [LICENSE](LICENSE).

Contributions welcome: [CONTRIBUTING.md](CONTRIBUTING.md) · security reports: [SECURITY.md](SECURITY.md) · conduct: [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).
