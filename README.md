# hermes-vps — Bare-metal VPS Deployment for Hermes Agent

One-command installer + FastAPI Management REST API + systemd + Caddy reverse-proxy for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) on **Ubuntu 24.04**.

Inspired by the OpenClaw deployment pattern, rewritten around Hermes's Python stack.

## Features

- **One-command install** — `curl … | bash` sets up Hermes + dashboard + mgmt API
- **No Docker** — runs directly on the OS via systemd, saves 200-500 MB RAM
- **FastAPI Management API** — 42 endpoints for status/config/channels/cron/logs/CLI
- **22+ providers** — Anthropic, OpenAI, Nous Portal, OpenRouter, z.ai, Kimi, MiniMax, Xiaomi MiMo, HuggingFace, + custom
- **6 messaging channels** — Telegram, Discord, Slack, Signal, WhatsApp, Email
- **Auto SSL** — Let's Encrypt via Caddy, self-signed fallback for IP-only domains
- **Hostname auto-detect** — uses `hostname -f`, falls back to `<IP>.sslip.io`

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | \
  bash -s --
```

After ~6-8 minutes:

```
Dashboard:      https://<HOSTNAME>/
Management API: https://<HOSTNAME>/api
MGMT_API_KEY:   <64-char hex — save this>
```

## Architecture

```
Internet
  │
  ├── :80/:443 ── Caddy (systemd, Let's Encrypt)
  │                 ├── /login, /api/*    → Mgmt API  :9997 (FastAPI)
  │                 └── /                 → Dashboard :9119 (Hermes web)
  │
  └── :9997 (direct, UFW-allowed) ── Mgmt API
```

| Service | Binary | Port | Purpose |
|---------|--------|------|---------|
| `hermes-gateway.service` | `hermes gateway run` | — | Messaging bridge |
| `hermes-dashboard.service` | `hermes dashboard --host 127.0.0.1 --port 9119` | 9119 | Web UI (FastAPI) |
| `hermes-mgmt.service` | `uvicorn hermes_mgmt.main:app` | 9997 | Management REST API |
| `caddy.service` | `caddy` | 80, 443 | Reverse proxy + SSL |

## Filesystem layout

```
/opt/hermes/                       Install root
├── .env                           Tokens + API keys + domain
├── .hermes/                       HERMES_HOME (config.yaml, logs, data)
├── data/                          Runtime data
├── Caddyfile                      Caddy config (uses env vars from .env)
└── hermes-agent/                  Upstream Hermes source (git clone, uv venv)

/opt/hermes-mgmt/                  Management API
├── pyproject.toml
├── hermes_mgmt/                   Python package
└── .venv/                         uv-managed venv

/etc/hermes/config/                Provider + channel JSON templates
├── anthropic.json
├── openai.json
├── nous-portal.json
├── openrouter.json
├── ...
└── channels/
    ├── telegram.json
    └── ...

/etc/systemd/system/
├── hermes.target
├── hermes-gateway.service
├── hermes-dashboard.service
├── hermes-mgmt.service
└── caddy.service.d/override.conf
```

## Daily ops

```bash
MGMT_KEY=$(grep ^HERMES_MGMT_API_KEY /opt/hermes/.env | cut -d= -f2)

# Status
systemctl status hermes-gateway hermes-dashboard hermes-mgmt caddy
curl -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/status

# Logs
journalctl -u hermes-gateway -f
journalctl -u hermes-mgmt -f
curl -H "Authorization: Bearer $MGMT_KEY" "http://localhost:9997/api/logs?service=hermes-gateway&lines=100"

# Configure messaging + model (first time)
hermes gateway setup
hermes model

# Via API
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"anthropic","model":"anthropic/claude-sonnet-4-6"}' \
  http://localhost:9997/api/config/provider

curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token":"<telegram-bot-token>"}' \
  http://localhost:9997/api/channels/telegram

# Restart
curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/restart

# Upgrade Hermes
curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/upgrade
```

## Management API — endpoint summary

Full catalog at `/openapi.json` or `docs/api-reference.md`.

| Category | Endpoints |
|----------|-----------|
| Public | `GET /health`, `GET /login`, `POST /api/auth/login`, `POST /api/auth/logout` |
| Info & status | `/api/info`, `/api/status`, `/api/version`, `/api/system`, `/api/domain` |
| Control | `/api/restart`, `/api/stop`, `/api/start`, `/api/rebuild`, `/api/upgrade`, `/api/reset` |
| Config | `/api/config`, `/api/providers`, `/api/config/provider`, `/api/config/api-key`, `/api/config/test-key` |
| Channels | `/api/channels`, `/api/channels/{channel}` (PUT/DELETE) |
| Cron | `/api/cron`, `/api/cron/{id}` pause/resume/run/remove |
| Logs | `/api/logs`, `/api/logs/stream` (SSE), `/api/logs/files` |
| Auth | `/api/auth/create-user`, `/api/auth/user`, `/api/auth/change-password` |
| Env | `/api/env`, `/api/env/{key}` (PUT/DELETE) |
| CLI | `POST /api/cli` — run whitelisted `hermes` subcommand |

All protected endpoints require `Authorization: Bearer <HERMES_MGMT_API_KEY>` or a session cookie from `/login`.

## Security

- **Ports:** UFW allows 22 (rate-limited), 80, 443, 9997 — everything else denied
- **Tokens:** 64-char hex via `openssl rand -hex 32` (`HERMES_GATEWAY_TOKEN`, `HERMES_MGMT_API_KEY`, `HERMES_MGMT_SESSION_SECRET`)
- **Password hashing:** bcrypt (cost factor 12) for `/login` password
- **Rate limiting:** 10 failed logins per IP per 15 min → 429
- **API key masking:** all GET responses return `sk-****<last4>`
- **HSTS + CORS:** explicit allowlist (no wildcards), strict headers
- **fail2ban:** SSH brute-force protection

## Troubleshooting

### Dashboard / gateway won't start

```bash
journalctl -u hermes-dashboard --no-pager -n 50
journalctl -u hermes-gateway --no-pager -n 50

# First-time setup (if no provider configured):
hermes gateway setup
hermes model
hermes config show
```

### SSL failed

```bash
journalctl -u caddy --no-pager -n 50
dig +short $(grep ^DOMAIN /opt/hermes/.env | cut -d= -f2)
# Ensure DNS A record points to VPS IP, then:
systemctl restart caddy
```

### Upgrade rollback

```bash
# Hermes rollback to previous version
cd /opt/hermes/hermes-agent
git log -n 5 --oneline
git checkout <previous-sha>
/opt/hermes/hermes-agent/.venv/bin/uv pip install -e '.[web,messaging,cron,voice,mcp,honcho]'
systemctl restart hermes-gateway hermes-dashboard
```

### Reset to clean state

```bash
curl -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"confirm":"RESET"}' \
  http://localhost:9997/api/reset
```

## Development

```bash
cd management-api
uv venv .venv --python 3.11
source .venv/bin/activate
uv pip install -e '.[dev]'
pytest -q tests/        # 63 tests
```

## License

MIT. See `LICENSE`.

Not affiliated with Nous Research. `hermes-agent` itself is MIT-licensed by Nous Research.
