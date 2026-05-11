# hermes-vps — Bare-metal VPS Deployment for Hermes Agent

One-command installer + FastAPI Management REST API + systemd + Caddy reverse-proxy for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) on **Ubuntu 24.04**.

Inspired by the OpenClaw deployment pattern, rewritten around Hermes's Python stack.

## Features

- **One-command install** — `curl … | bash` sets up Hermes + dashboard + mgmt API in ~3 min
- **No Docker** — runs directly on the OS via systemd, saves 200-500 MB RAM
- **FastAPI Management API** — 42 endpoints for status/config/channels/cron/logs/CLI (smoke-tested 34/34 PASS, see [#api-test-results](#api-test-results))
- **15 provider templates** — Anthropic, OpenAI, Google, xAI, DeepSeek, Groq, Mistral, Together, Nous Portal, OpenRouter, HuggingFace, Kimi, MiMo, MiniMax, z.ai
- **6 messaging channels** — Telegram, Discord, Slack, Signal, WhatsApp, Email
- **Auto SSL** — Let's Encrypt via Caddy, self-signed fallback when DNS isn't ready
- **Dashboard auth gate** — Caddy enforces a `?token=<HERMES_AUTH_TOKEN>` query then drops a 30-day HTTP-only cookie; anyone without token gets 403
- **Hostname auto-detect** — uses `hostname -f`, falls back to `<IP>.sslip.io`

## Quickstart

```bash
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | \
  bash -s --
```

After ~3 minutes:

```
Dashboard URL (first visit, sets a 30d cookie then strips token):
  https://<HOSTNAME>/?token=<AUTH_TOKEN>

Management API: http://<IP>:9997     (port-only, not proxied via Caddy)

AUTH_TOKEN:     <48-char hex — dashboard gate>
MGMT_API_KEY:   <64-char hex — mgmt API bearer>
GATEWAY_TOKEN:  <64-char hex — hermes gateway>
```

Open the dashboard URL once in each browser; Caddy will set the
`hermes_auth` cookie (`HttpOnly; Secure; SameSite=Lax; Max-Age=2592000`)
and 302-redirect to `/` so the token never lands in browser history.
Subsequent visits to `https://<HOSTNAME>/` are auto-authenticated by
cookie. Visitors without the token receive `403 Forbidden`.

## Architecture

```
Internet
  │
  ├── :80/:443 ── Caddy (systemd, Let's Encrypt)
  │                 │
  │                 ├── ?token=<AUTH_TOKEN>  → 302 + Set-Cookie hermes_auth (30d)
  │                 ├── cookie hermes_auth   → reverse_proxy 127.0.0.1:9119
  │                 │                          (Host header rewritten to localhost:9119
  │                 │                          to satisfy Hermes' strict Host check)
  │                 └── neither              → 403 Forbidden
  │
  └── :9997 (direct, UFW-allowed) ── Mgmt API (Bearer auth)
```

Hermes' dashboard owns the entire `/api/*` surface (status, sessions,
env, providers, logs, …). The management API is **not** proxied through
Caddy because its `/api/*` namespace overlaps with Hermes'; it stays on
port `9997` and is reached directly.

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

/etc/hermes/config/                Provider + channel JSON templates (15+6)
├── anthropic.json    deepseek.json    google.json       groq.json
├── huggingface.json  kimi.json        mimo.json         minimax.json
├── mistral.json      nous-portal.json openai.json       openrouter.json
├── together.json     xai.json         zai.json
└── channels/
    ├── discord.json  email.json       signal.json
    ├── slack.json    telegram.json    whatsapp.json

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

## Management API — full reference

Base URL: `http://<VPS-IP>:9997` (UFW-opened, not behind Caddy).
Live OpenAPI spec: `GET $BASE/openapi.json`.

### Authentication

All routes except `GET /health`, `POST /api/auth/login` and `POST /api/auth/logout`
require **one** of:

- **Bearer token** — `Authorization: Bearer $HERMES_MGMT_API_KEY` (long-lived, from `.env`)
- **Session cookie** — set by `POST /api/auth/login` (bcrypt password, HMAC-signed session)

Rate limit: **10 failed logins per IP / 15 min → HTTP 429**.

### Response envelope

Every JSON response uses the same shape:

```json
{ "ok": true,  "data": { ... }, "error": null }
{ "ok": false, "data": null,    "error": "Description of failure" }
```

Validation errors return FastAPI's standard `422` with `{ "detail": [...] }`.

### Endpoint catalog (42 routes)

#### 1) Health & info (public + bearer)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | Liveness probe — returns `{"ok": true, "version": "0.1.0"}` |
| GET | `/api/info` | bearer | Domain, IP, Hermes + mgmt versions, dashboard URL |
| GET | `/api/status` | bearer | systemd active/inactive state for each Hermes service |
| GET | `/api/version` | bearer | Full `hermes version` output |
| GET | `/api/system` | bearer | CPU%, memory, disk, uptime, load avg (via `psutil`) |
| GET | `/api/domain` | bearer | Current `DOMAIN` from `.env` |

```bash
curl -s -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/system
# { "ok": true, "data": {
#     "cpu_percent": 0.0,
#     "memory": {"total": 4105515008, "available": 3371732992, "percent": 17.9},
#     "disk":   {"total": 50884108288, "used": 4532400128, "percent": 8.9},
#     "uptime_seconds": 280146.58,
#     "load_avg": [0.28, 0.10, 0.03]
# }, "error": null }
```

#### 2) Authentication (6)

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/auth/login` | `{username, password}` | Returns session cookie. 401 on wrong password, 429 after 10 failures |
| POST | `/api/auth/logout` | — | Idempotent; clears session cookie |
| GET | `/api/auth/user` | — | Current authenticated user |
| POST | `/api/auth/create-user` | `{username, password}` | Create additional admin |
| PUT | `/api/auth/change-password` | `{old_password, new_password}` | Rotate password |
| DELETE | `/api/auth/user` | — | Remove the current user account |

#### 3) Service control (6) — **destructive**

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/restart` | — | `systemctl restart hermes.target` |
| POST | `/api/stop` | — | Stop all Hermes services |
| POST | `/api/start` | — | Start all Hermes services |
| POST | `/api/rebuild` | — | `cd web && npm install && npm run build`, then restart dashboard |
| POST | `/api/upgrade` | — | `git pull` Hermes + `uv pip install -e '.[…]'` + restart |
| POST | `/api/reset` | `{"confirm":"RESET"}` | Wipe config + sessions (requires explicit confirm string) |
| PUT | `/api/domain` | `{domain}` | Change `DOMAIN` in `.env`, re-renders Caddyfile, restarts Caddy |

#### 4) Config (5)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/config` | — | Current `config.yaml` content (API keys masked as `sk-****<last4>`) |
| GET | `/api/providers` | — | Lists all `*.json` templates in `/etc/hermes/config/` (15 by default) |
| PUT | `/api/config/provider` | `{provider, model}` | Set `model.primary`. Strips a duplicate `<provider>/` prefix if caller already added it |
| PUT | `/api/config/api-key` | `{provider, api_key}` | Writes `<PROVIDER>_API_KEY` to `.env`, restarts gateway |
| DELETE | `/api/config/api-key?provider=<p>` | — | Remove API key from `.env` |
| POST | `/api/config/test-key` | `{provider, api_key}` | `GET <base_url>/v1/models` (or provider-specific path) — does **not** save the key |

```bash
# Test a key without saving
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"openai","api_key":"sk-..."}' \
  http://localhost:9997/api/config/test-key
# { "ok": true, "data": {"status_code": 200, "provider": "openai"}, "error": null }

# Set the active model (both forms are accepted and produce the same result)
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"deepseek","model":"deepseek-v4-flash"}' \
  http://localhost:9997/api/config/provider
# Equivalent:
#   -d '{"provider":"deepseek","model":"deepseek/deepseek-v4-flash"}'
```

#### 5) Channels (3)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/channels` | — | List configured messaging channels |
| PUT | `/api/channels/{channel}` | provider-specific (e.g. `{token, chat_id}` for Telegram) | Upsert channel config in `config.yaml` |
| DELETE | `/api/channels/{channel}` | — | Remove channel. Returns 404 if not configured |

Supported `{channel}`: `telegram`, `discord`, `slack`, `signal`, `whatsapp`, `email`.

#### 6) Cron (6)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/cron` | — | List all `hermes cron` jobs |
| GET | `/api/cron/status` | — | Aggregate status (running, paused, failed counts) |
| POST | `/api/cron` | `{name, schedule, prompt, ...}` | Create new job |
| POST | `/api/cron/{job_id}/pause` | — | Pause job |
| POST | `/api/cron/{job_id}/resume` | — | Resume paused job |
| POST | `/api/cron/{job_id}/run` | — | Trigger job immediately |
| DELETE | `/api/cron/{job_id}` | — | Remove job |

#### 7) Logs (3)

| Method | Path | Query | Description |
|---|---|---|---|
| GET | `/api/logs` | `service`, `lines`, `priority` | journalctl tail for one service |
| GET | `/api/logs/stream` | `service` | **SSE** stream — emits new lines as they arrive |
| GET | `/api/logs/files` | — | List files in `/var/log/{caddy,hermes-install.log,…}` |

```bash
# Tail gateway logs (last 200)
curl -s -H "Authorization: Bearer $MGMT_KEY" \
  "http://localhost:9997/api/logs?service=hermes-gateway&lines=200"

# Live stream (SSE)
curl -N -H "Authorization: Bearer $MGMT_KEY" \
  "http://localhost:9997/api/logs/stream?service=hermes-gateway"
```

#### 8) Environment (3)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/env` | — | All `/opt/hermes/.env` keys (sensitive values masked) |
| PUT | `/api/env/{key}` | `{value}` | Upsert single env var, atomic write |
| DELETE | `/api/env/{key}` | — | Remove single env var |

Keys matching `/(_KEY|_TOKEN|_SECRET|_PASSWORD|_HASH)$/i` are returned as `sk-****<last4>` in GET responses.

#### 9) Hermes CLI passthrough (1)

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/cli` | `{subcommand, args}` | Run a whitelisted `hermes <sub>` command and return stdout/stderr/exit code |

Whitelist: `version, status, doctor, config, model, cron, gateway, logs, skills, sessions, memory, tools, insights, auth`. Anything else returns `400`.

```bash
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"subcommand":"version","args":[]}' \
  http://localhost:9997/api/cli
# { "ok": true, "data": {"exit_code": 0, "stdout": "Hermes Agent v0.13.0 …", "stderr": ""}, "error": null }
```

### API test results

Smoke-tested against a live VPS install on **2026-05-11**:

```
PASS: 34/34 tested endpoints
FAIL: 0
SKIP: 8  (destructive control + SSE stream — schema validated only)
```

| Category | Result |
|---|---|
| Health / info (6 GETs) | ✅ all 200, JSON shape verified |
| Auth (5 endpoints) | ✅ login rejects bad password (401), empty body → 422, logout idempotent |
| Config (6 endpoints) | ✅ 15 providers listed; `PUT /api/config/provider` is idempotent for both bare and prefixed model strings (regression bug fixed in commit `64fd29f`) |
| Channels (2 endpoints) | ✅ 404 on nonexistent channel; empty PUT body → 422 |
| Cron (3 endpoints) | ✅ list + status; empty POST body → 422 |
| Logs (2 endpoints) | ✅ tail + files |
| Env (3 endpoints) | ✅ transient PUT + DELETE roundtrip |
| CLI (1 endpoint) | ✅ `hermes version` passthrough |
| Path-param 404s (6) | ✅ deletes/pauses on `__nonexistent__` ID handled gracefully |

Smoke test script: [`scripts/test-mgmt-api.sh`](scripts/test-mgmt-api.sh) (mirrored at `/tmp/test-mgmt-api.sh` during deploy).

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
