# hermes-vps — Bare-metal VPS Deployment for Hermes Agent

One-command installer + FastAPI Management REST API + systemd + Caddy reverse-proxy for [NousResearch/hermes-agent](https://github.com/NousResearch/hermes-agent) on **Ubuntu 24.04**.

Inspired by the OpenClaw deployment pattern, rewritten around Hermes's Python stack.

## Features

- **One-command install** — `curl … | bash` sets up Hermes + dashboard + mgmt API in ~3 min
- **No Docker** — runs directly on the OS via systemd, saves 200-500 MB RAM
- **FastAPI Management API** — 73 endpoints for status/config/channels/cron/logs/CLI/Zalo/OpenViking/Codex/Roles (smoke-tested 34/34 PASS, see [#api-test-results](#api-test-results))
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

## RAG knowledge base (optional)

Give Hermes retrieval over **your own documents**. Install with the `--with-rag`
flag to add a 4th service (`hermes-rag`) that ingests local files and exposes an
MCP `rag_search` tool the agent calls during chat:

```bash
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | \
  bash -s -- --with-rag
```

Then load your documents and index them:

```bash
cp ~/my-docs/*.{md,txt,pdf} /opt/hermes-rag/docs/
hermes-rag ingest        # chunk + embed + store
hermes-rag stats         # documents / chunks / model
hermes-rag search "câu hỏi của bạn"   # one-off test query
```

- **Embeddings run locally** via fastembed (CPU, no API key, offline). Default
  model is multilingual and works well for Vietnamese. DeepSeek/your chat model
  is unaffected — it still does the answering; RAG only does retrieval.
- **No native deps**: plain SQLite + numpy cosine. Good to ~tens of thousands of
  chunks; see [rag-mcp/README.md](rag-mcp/README.md) for tuning and the larger
  `multilingual-e5-large` model option.
- Port `9998` is **loopback-only** (not opened in UFW); Hermes connects over
  localhost and auto-reloads the `mcp_servers.rag` entry the installer adds to
  `config.yaml`.

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
├── .env                           Service tokens + domain (systemd EnvironmentFile)
├── data/                          Runtime data
├── Caddyfile                      Caddy config (uses env vars from .env)
└── hermes-agent/                  Upstream Hermes source (git clone, uv venv)

/root/.hermes/                     HERMES_HOME — Hermes's own store
├── config.yaml                    Model, terminal, display, ... config
├── .env                           Provider API keys (ANTHROPIC_API_KEY, ...)
├── sessions/, logs/, skills/      Runtime data
└── auth.json                      OAuth tokens

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

# Upgrade Hermes Agent (git pull + reinstall + restart gateway+dashboard)
curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/upgrade

# Upgrade management API itself (re-pulls /opt/hermes-mgmt sources + restart hermes-mgmt)
curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/upgrade-mgmt

# Get one-click dashboard URL (with ?token= for first visit, sets cookie)
curl -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/info \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['data']['dashboard_url'])"
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

### Endpoint catalog (73 routes)

#### 1) Health & info (public + bearer)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | none | Liveness probe — returns `{"ok": true, "version": "0.1.0"}` |
| GET | `/api/info` | bearer | Domain, public IP, Hermes + mgmt versions, **one-click `dashboard_url` with `?token=`**, raw `auth_token` |
| GET | `/api/status` | bearer | systemd active/inactive state for each Hermes service |
| GET | `/api/version` | bearer | Full `hermes version` output |
| GET | `/api/system` | bearer | CPU%, memory, disk, uptime, load avg (via `psutil`) |
| GET | `/api/domain` | bearer | Current `DOMAIN` from `.env` |

```bash
curl -s -H "Authorization: Bearer $MGMT_KEY" http://<VPS-IP>:9997/api/info
# { "ok": true, "data": {
#     "domain":         "wxfparstk.tino.page",
#     "ip":             "103.142.27.98",     # resolved live, not 127.0.0.1
#     "hermes_version": "Hermes Agent v0.13.0 (2026.5.7) ...",
#     "mgmt_version":   "0.1.0",
#     "dashboard_url":  "https://wxfparstk.tino.page/?token=<AUTH_TOKEN>",
#     "auth_token":     "<AUTH_TOKEN>"       # null if HERMES_AUTH_TOKEN unset
# }, "error": null }
```

Provisioning systems (Hostbill, n8n, ...) can pass `dashboard_url`
directly to end customers as a one-click link; Caddy sets the
`hermes_auth` cookie on first visit and the token is stripped from
browser history via the 302 redirect.

```bash
curl -s -H "Authorization: Bearer $MGMT_KEY" http://<VPS-IP>:9997/api/system
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

#### 3) Service control (7) — **destructive**

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/restart` | — | `systemctl restart hermes.target` |
| POST | `/api/stop` | — | Stop all Hermes services |
| POST | `/api/start` | — | Start all Hermes services |
| POST | `/api/rebuild` | — | `cd web && npm install && npm run build`, then restart dashboard |
| POST | `/api/upgrade` | — | `git pull` Hermes Agent + `uv pip install -e '.[…]'` + restart gateway/dashboard. Returns 202 Accepted; runs in background |
| POST | `/api/upgrade-mgmt` | — | **Self-update**: re-pulls all `/opt/hermes-mgmt/` sources from raw GitHub (or `git pull` if checked out), reinstalls via `uv pip install -e .`, restarts `hermes-mgmt.service`. Returns 202 before uvicorn restarts |
| POST | `/api/reset` | `{"confirm":"RESET"}` | Wipe config + sessions (requires explicit confirm string) |
| PUT | `/api/domain` | `{domain}` | Change `DOMAIN` in `.env`, re-renders Caddyfile, restarts Caddy |

```bash
# Bootstrap the upgrade endpoint itself the first time (mgmt-api was installed
# before /api/upgrade-mgmt existed). All subsequent upgrades can use the API.
ssh root@<VPS> 'curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/scripts/upgrade-mgmt.sh | bash'

# Once /api/upgrade-mgmt is live, upgrade in place via API:
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" \
  http://<VPS-IP>:9997/api/upgrade-mgmt
# { "ok": true, "data": {"message": "Management API upgrade started in background"} }
```

#### 4) Config (5)

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/config` | — | Current `config.yaml` content (API keys masked as `sk-****<last4>`) |
| GET | `/api/providers` | — | Lists all `*.json` templates in `/etc/hermes/config/` (15 by default) |
| PUT | `/api/config/provider` | `{provider, model}` | Sets `model.default` (`<provider>/<bare-model>`) and `model.provider` in `config.yaml`. Strips a duplicate `<provider>/` prefix if caller already added it |
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

#### 10) Zalo personal connect (6)

The Zalo plugin ships a Node.js sidecar (zca-js) bound to `127.0.0.1:3838` —
**unreachable from outside the VPS**. These routes proxy it through the
Management API so a low-tech user connects Zalo entirely from the dashboard:
**scan QR → enter the boss's phone → done**. No SSH, no `curl`, no UID hunting.

**Bot vs owner (important):** the QR-scanned account is the **BOT** (a secondary
number that sends/receives on the bot's behalf). The **owner** is the **boss** —
a *different* Zalo account who messages the bot to give admin commands. So after
QR login you must tell the API who the owner is, by **phone number** (the API
resolves it to a UID via the sidecar). The Hermes plugin won't run the platform
until `ZALO_PERSONAL_OWNER_UID` is set; setting the owner enables the plugin and
restarts the gateway, which then manages the sidecar (login persists on disk).

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/zalo/connect` | — | Start QR login. Spawns the sidecar if needed. `{status:"pending", qr_url:"/api/zalo/qr"}` (or `{status:"connected", bot_uid}` if already logged in). `503` if sidecar can't start |
| GET | `/api/zalo/qr` | — | Raw QR **PNG bytes** (use as `<img src>`). `404` while QR still generating; retry 1–2s after `/connect` |
| GET | `/api/zalo/status` | — | Poll this. `data`: `status` ∈ `disconnected/pending/scanned/connected/error`, `bot_uid` (the scanned bot account — **not** the owner), `name`, `sidecar` (bool), `owner_set` (bool — has the boss's owner UID been configured) |
| POST | `/api/zalo/set-owner` | `{phone}` or `{uid}` | Set the boss as owner. `phone` is resolved to a UID via the connected sidecar (`/users/by-phones`); `uid` sets it directly. Then enables the plugin + restarts gateway. `409` if bot not logged in, `404` if phone has no Zalo account |
| GET | `/api/zalo/owner` | — | Current owner: `{owner_uid, owner_set}` |
| POST | `/api/zalo/disconnect` | — | Logout + clear the Zalo session |

Dashboard flow:

```
[Kết nối Zalo]  → POST /api/zalo/connect
                → poll GET /api/zalo/status every ~2s
   pending      → show <img src="/api/zalo/qr"> + "Quét bằng app Zalo (số phụ)"
   connected, owner_set=false
                → ask: "Nhập số Zalo của SẾP (người điều khiển bot)"
                → POST /api/zalo/set-owner {phone}
   owner_set=true → "✅ Bot sẵn sàng. Sếp nhắn bot để ra lệnh; khách nhắn được tiếp."
```

```bash
# 1. Start QR login (spawns sidecar), then render /api/zalo/qr; scan with the
#    SECONDARY Zalo number that will be the bot.
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/zalo/connect

# 2. Poll until connected (bot_uid = the bot account, NOT the owner)
curl -s -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/zalo/status
# { "ok": true, "data": {"status":"connected","bot_uid":"555","sidecar":true,"owner_set":false}, ... }

# 3. Set the boss as owner by their phone number (resolved to a UID by the bot)
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"phone":"0901234567"}' http://localhost:9997/api/zalo/set-owner
# { "ok": true, "data": {"owner_uid":"boss123","owner_set":true}, "error": null }
```

> ⚠️ Unofficial Zalo Web API. **Use a secondary number for the bot** — bulk
> friend/message actions risk account bans. Installed + enabled by default
> (`--skip-zalo` to opt out). Anyone can chat the bot without per-user approval
> (`ZALO_PERSONAL_ALLOW_ALL_USERS=true`); the owner gets admin commands.
> See `/root/.hermes/plugins/zalo-personal/README.md`.

#### 11) OpenViking memory backend (12)

[OpenViking](https://github.com/volcengine/OpenViking) is an optional context
database / memory backend — **not installed by default** (it needs extra RAM +
an embedding/VLM LLM key). The dashboard drives the full lifecycle on demand, so
each customer decides whether to install it. Runs in an isolated venv at
`/opt/hermes-openviking` under its own `hermes-openviking.service` on
`127.0.0.1:1933`, wired into Hermes via `OPENVIKING_ENDPOINT`.

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/openviking/status` | — | Lifecycle state: `installed`, `config_ready`, `service_active`, `healthy`, `wired_into_hermes`, `endpoint` |
| POST | `/api/openviking/install` | — | Run the installer in the background (venv + `pip install openviking` + systemd unit). `202`; reuses an existing `OPENAI_API_KEY` from `.env` |
| GET | `/api/openviking/config` | — | Current `ov.conf` (embedding + VLM), `api_key` masked. `{configured:false}` if not set yet |
| POST | `/api/openviking/config` | `{api_key, api_base?, embedding_model?, vlm_model?, provider?, dimension?}` | Write `~/.openviking/ov.conf` (embedding + VLM). A single `api_key` is applied to both |
| POST | `/api/openviking/test-key` | `{api_key, api_base?}` | Validate the LLM key (GET `<api_base>/models`) before saving. Does not persist |
| POST | `/api/openviking/enable` | — | Start the service + set `OPENVIKING_ENDPOINT` + restart gateway. `409` if not installed or not configured |
| POST | `/api/openviking/disable` | — | Stop the service + remove `OPENVIKING_ENDPOINT` + restart gateway (keeps the install) |
| POST | `/api/openviking/restart` | — | Restart `hermes-openviking.service` (recover a hung server) |
| POST | `/api/openviking/upgrade` | — | `pip install --upgrade openviking` (background) + restart. `202` |
| GET | `/api/openviking/stats` | — | `service_active`, `active_since`, `data_dir`, `data_size_mb`, live `server_stats` (best-effort) |
| POST | `/api/openviking/uninstall` | `{purge?: bool}` | Unwire + stop + run uninstaller in background. `purge=true` also deletes config + data |
| GET | `/api/openviking/logs` | `lines` (query, default 100) | journal tail of `hermes-openviking.service` |

Dashboard flow:

```
[Bật Memory nâng cao]
  → POST /api/openviking/install        (poll /status until installed=true)
  → POST /api/openviking/config {api_key}   (or rely on the reused OPENAI_API_KEY)
  → POST /api/openviking/enable         (poll /status until healthy=true)
[Tắt]      → POST /api/openviking/disable
[Gỡ]       → POST /api/openviking/uninstall {"purge": true}
```

```bash
# Install (background) then check
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/openviking/install
curl -s -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/openviking/status
# { "ok": true, "data": {"installed": true, "config_ready": true,
#     "service_active": false, "healthy": false, "wired_into_hermes": false,
#     "endpoint": "http://127.0.0.1:1933"}, "error": null }

# Configure (only if no usable OPENAI_API_KEY was reused) + enable
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"api_key":"sk-..."}' http://localhost:9997/api/openviking/config
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/openviking/enable
```

#### 12) OpenAI Codex OAuth (4)

Log the bot into **OpenAI Codex** (provider `openai-codex`) without an API key,
straight from the dashboard. Codex uses a **device-code** flow: the API starts
`hermes auth add openai-codex` headless, returns a URL + short code; the user
opens the URL, enters the code, and the background process writes the token to
`~/.hermes/auth.json`. On success the bot is switched to the `codex` provider
automatically.

| Method | Path | Body | Description |
|---|---|---|---|
| POST | `/api/codex/auth/start` | — | Start device-code login. Returns `{status:"pending", url, code}` — show both to the user. The process keeps polling in the background |
| GET | `/api/codex/auth/status` | — | `disconnected` / `pending` (with `url`+`code`) / `connected`. On first `connected`, sets `config.yaml` model.provider=codex + restarts gateway |
| POST | `/api/codex/auth/import` | `{auth_json}` | Fallback — paste an `auth.json` from Codex CLI / another machine instead of the device flow. Validates it has a codex entry, then sets model + restarts |
| POST | `/api/codex/auth/disable` | `{to_provider?}` | Disconnect Codex: `hermes auth remove openai-codex`, clear codex from `auth.json` + `active_provider`, repoint `config.yaml` model.provider (to `to_provider` or none), restart gateway. Needed because Codex's `active_provider` overrides config — without removing it the dashboard can't switch providers |

Dashboard flow:

```
[Đăng nhập Codex]
  → POST /api/codex/auth/start          → show url + code
  → user opens url, enters code in browser
  → poll GET /api/codex/auth/status     → connected (bot now uses Codex)
```

```bash
# 1. Start — show the url + code to the user
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/codex/auth/start
# { "ok": true, "data": {"status": "pending",
#     "url": "https://auth.openai.com/codex/device", "code": "41R1-F6HEA"}, "error": null }

# 2. Poll until the user finishes in the browser
curl -s -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/codex/auth/status
# { "ok": true, "data": {"status": "connected", "model_set": true}, "error": null }
```

#### 13) Agent roles & rule policies (8)

Give the agent a **role** (CSKH, sales, marketing, receptionist, or a custom
one) from the dashboard. Each role = persona + tone + a set of **rule groups**.
Rule groups are markdown files (`config/rules/*.md`, 8 groups A–H); roles are
yaml (`config/roles/*.yaml` preset, `HERMES_HOME/roles/*.yaml` custom). Applying
a role assembles a system prompt (persona + every enabled rule body), writes it
to `HERMES_HOME/persona.md`, records `active_role.json`, and restarts the gateway.

Rule groups (toggled per role): `a-identity`, `b-account-safety`,
`c-anti-spam-content`, `d-security-privacy`, `e-marketing-sales`,
`f-conversation-quality`, `g-tools-actions`, `h-operations-escalation`.

| Method | Path | Body | Description |
|---|---|---|---|
| GET | `/api/rules` | — | List all rule groups (`id`, `title`, full markdown `body`) |
| GET | `/api/rules/{group_id}` | — | One rule group's markdown |
| GET | `/api/roles` | — | List roles (preset + custom) + the active role id |
| GET | `/api/roles/active` | — | Currently applied role (`id`, `rules`, `applied_at`) |
| GET | `/api/roles/{role_id}` | — | Role detail (persona, tone, rules, source) |
| POST | `/api/roles` | `{id, label?, description?, emoji?, tone?, persona, rules:[group_id...]}` | Create/update a **custom** role. Unknown rule ids are dropped; cannot shadow a preset id |
| DELETE | `/api/roles/{role_id}` | — | Delete a custom role (presets are read-only → 403) |
| POST | `/api/roles/{role_id}/apply` | — | Build persona + rules → write `persona.md` → restart gateway. Returns `persona_preview` |

Dashboard flow:

```
GET /api/roles                       → render role cards (active highlighted)
GET /api/rules                        → render the 8 rule-group toggles
[Tạo role]   → POST /api/roles {id, persona, rules:[...]}
[Áp dụng]    → POST /api/roles/{id}/apply   → bot adopts the role
```

```bash
# List roles + rule groups (for the GUI)
curl -s -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/roles
curl -s -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/rules

# Create a custom role picking which rule groups to enforce
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"id":"spa","label":"Lễ tân Spa","persona":"Bạn là lễ tân spa...",
       "rules":["a-identity","d-security-privacy","f-conversation-quality"]}' \
  http://localhost:9997/api/roles

# Apply it (assembles persona+rules, restarts gateway)
curl -s -X POST -H "Authorization: Bearer $MGMT_KEY" \
  http://localhost:9997/api/roles/spa/apply
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
