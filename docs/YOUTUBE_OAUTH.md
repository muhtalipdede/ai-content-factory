# YouTube OAuth

## Shared Desktop client

1. In Google Cloud Console, enable **YouTube Data API v3**.
2. Create an OAuth **Desktop** client.
3. Download the JSON and save it as:

```
credentials/client_secret.json
```

This file is gitignored. Never commit it.

## Per-project token

Authorize once per channel account:

```bash
python jobs/run_episode.py -p demo_facts auth-youtube
```

Token path: `projects/<id>/credentials/youtube_token.json` (also gitignored).

### Troubleshooting

- Use `--console` if the browser consent page fails:  
  `python jobs/run_episode.py -p demo_facts auth-youtube --console`
- Use `--force` to revoke the local token and re-consent.

## Publishing safety

- Prefer `privacy_status: "private"` or `"unlisted"` while testing.
- Keep `AUTO_PUBLISH=false` / `--dry-run` / `--skip-publish` until quality gates look good.
