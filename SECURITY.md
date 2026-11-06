# Security Policy

## Reporting a vulnerability

Please report security issues privately (GitHub Security Advisories if enabled, or a private email to the maintainer). Do not open a public issue for leaked credentials or exploit details.

## Secrets

Never commit:

- `.env` or API keys
- `credentials/client_secret.json`
- `projects/*/credentials/youtube_token.json`
- Generated episode media under `projects/*/data/episodes/`

If a secret is exposed, rotate it immediately (OpenAI keys, Google OAuth client, YouTube refresh tokens).

## Safe defaults

This project defaults to `DRY_RUN=true` and `AUTO_PUBLISH=false` in `.env.example`. Prefer private uploads while testing.
