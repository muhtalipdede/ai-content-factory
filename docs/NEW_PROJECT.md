# Create a new project

Each YouTube channel (or experiment) lives under `projects/<id>/`.

## Steps

```bash
cp -R projects/_template projects/my_channel
# edit projects/my_channel/project.json
# edit projects/my_channel/brand/*
```

Important `project.json` knobs:

| Key | Purpose |
|-----|---------|
| `language` | Narration language (`en`, `tr`, …) |
| `made_for_kids` | YouTube Made for Kids flag |
| `speakers_mode` | `narrator_only` or character cast |
| `requires_sources` | Enforce `sources[]` URLs |
| `verify_claims` | Claim-vs-page overlap gate |
| `min_sources` | Minimum accepted sources |
| `min_duration_sec` / `max_duration_sec` | Script length window |
| `burn_captions` | Burn captions into the final video |
| `tts_voice` / `tts_instructions` | OpenAI TTS personality |
| `title_templates` | Ideation title patterns |
| `ideation_system` / `script_system` | LLM system prompts |
| `default_tags` / `description_footer` | YouTube metadata |

Projects whose folder name starts with `_` (like `_template`) are hidden from `list_projects()`.

## Local scripts

Put JSON episodes in `projects/<id>/data/scripts/queue/`. Validate with:

```bash
python jobs/run_episode.py -p my_channel scripts --validate
```

## Bed music

Drop a royalty-free `.mp3` into `projects/<id>/assets/music/`. Do not commit tracks you cannot redistribute.
