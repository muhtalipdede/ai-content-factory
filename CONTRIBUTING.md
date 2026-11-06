# Contributing

Thanks for helping improve AI Content Factory.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# optional: set OPENAI_API_KEY for live dry-runs
```

System dependency: `ffmpeg` / `ffprobe` (`brew install ffmpeg` or distro equivalent).

## Tests

```bash
python -m unittest discover -s tests -v
```

Tests are offline (no API keys required).

## Pull requests

- Prefer small, focused PRs with a clear problem statement.
- Do not commit secrets, episode media, OAuth tokens, or personal channel configs.
- Keep the shared motor (`pipeline/`, `config.py`, `orchestrator.py`) project-agnostic.
- Channel-specific knobs belong in `projects/<id>/project.json` and `brand/`.
- Match existing code style; avoid drive-by refactors.

## New projects

See [docs/NEW_PROJECT.md](docs/NEW_PROJECT.md). Ship demos under a clearly fictional id — do not upstream private channel brand IP.
