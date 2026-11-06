# Getting started

## 1. Install

```bash
git clone https://github.com/<you>/ai-content-factory.git
cd ai-content-factory
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Install FFmpeg. Set `OPENAI_API_KEY` in `.env` when you want live generation.

## 2. Validate the demo queue

```bash
python jobs/run_episode.py -p demo_facts scripts --validate
```

## 3. Dry-run one episode

```bash
python jobs/run_episode.py -p demo_facts run \
  --script projects/demo_facts/data/scripts/queue/ep_demo_postit.json \
  --dry-run
```

Dry-run skips publish. Image/TTS calls still spend API budget unless providers are stubbed.

## 4. Optional YouTube auth

See [YOUTUBE_OAUTH.md](YOUTUBE_OAUTH.md). Keep `AUTO_PUBLISH=false` until you are ready.

## 5. Create your own channel project

See [NEW_PROJECT.md](NEW_PROJECT.md).
