# hermes-vps — Architecture for AI Coding Agents

## Overview

Bare-metal Ubuntu 24.04 VPS deployment wrapper for NousResearch Hermes Agent. Three systemd-managed services + Caddy reverse proxy + FastAPI Management API. Net-new Python codebase, not a fork of OpenClaw.

## Service graph

```
Internet :80/:443
       │
       ▼
┌───────────────────┐
│ caddy.service     │  apt-installed, systemd-override injects our Caddyfile
│ port 80, 443      │  Let's Encrypt auto via {$DOMAIN}; self-signed fallback via {$CADDY_TLS}
└────────┬──────────┘
         │
  ┌──────┴──────────────────────┬─────────────────────┐
  ▼                             ▼                     ▼
┌──────────────────┐  ┌──────────────────────┐  ┌──────────────────────┐
│ hermes-gateway   │  │ hermes-dashboard     │  │ hermes-mgmt          │
│ (Hermes CLI)     │  │ (Hermes CLI)         │  │ (FastAPI / uvicorn)  │
│ `hermes gateway  │  │ `hermes dashboard    │  │ hermes_mgmt.main:app │
│  run` — blocking │  │  --no-open --host    │  │                      │
│                  │  │  127.0.0.1 --port    │  │ :9997                │
│ No inbound port  │  │  9119`               │  │                      │
│ (outbound dialer)│  │ :9119                │  │                      │
└──────────────────┘  └──────────────────────┘  └──────────────────────┘
```

All 3 services are grouped under `hermes.target` for atomic start/stop.

## Paths

| Path | Purpose |
|------|---------|
| `/opt/hermes/` | Install root; `HOME` override for Hermes processes |
| `/opt/hermes/.env` | All env vars — loaded by systemd `EnvironmentFile=` |
| `/opt/hermes/.hermes/` | `HERMES_HOME` — config.yaml, sessions, skills, logs |
| `/opt/hermes/hermes-agent/` | Upstream Hermes git clone (editable uv venv) |
| `/opt/hermes/Caddyfile` | Caddy config (uses `$DOMAIN` + `$CADDY_TLS` from .env) |
| `/opt/hermes-mgmt/` | Management API Python package + uv venv |
| `/etc/hermes/config/` | Read-only provider/channel JSON templates |
| `/etc/systemd/system/hermes.target` | Meta-target grouping 3 units |
| `/etc/systemd/system/hermes-*.service` | Unit files for each service |
| `/etc/systemd/system/caddy.service.d/override.conf` | Caddy EnvironmentFile + Caddyfile override |
| `/var/log/hermes-install.log` | install.sh transcript |
| `/var/log/caddy/access.log` | Caddy access log (rotated) |

## Critical invariants

1. **`hermes gateway run`** is blocking — use for systemd `Type=simple` ExecStart. Do NOT use `hermes gateway start` (that targets the CLI's own systemd-user service).
2. **`hermes dashboard`** is the web UI command (NOT `hermes web`). Default `127.0.0.1:9119`. Flag `--insecure` required to bind 0.0.0.0 (but we proxy via Caddy so we never do that).
3. **`HERMES_HOME`** env var drives all Hermes state — set to `/opt/hermes/.hermes` in every systemd unit.
4. **Hermes config is YAML** (`config.yaml`), not JSON. `hermes_mgmt.hermes_fs.read_config_yaml` parses it.
5. **`.env` is the single source of truth** for tokens/domain/keys. All 4 systemd units `EnvironmentFile=/opt/hermes/.env`. After edits, restart affected services.
6. **Management API auth:** `Authorization: Bearer <HERMES_MGMT_API_KEY>` or session cookie from `POST /api/auth/login`. Constant-time compare via `hmac.compare_digest`.
7. **CLI whitelist** for `POST /api/cli`: `version, status, doctor, config, model, cron, gateway, logs, skills, sessions, memory, tools, insights, auth` (see `hermes_mgmt.cli_runner.HERMES_WHITELIST`).
8. **Domain auto-detect** in install.sh: `hostname -f` if FQDN → that; else `<IP>.sslip.io`; override with `--domain` flag.

## Runtime commands

```bash
# Full install (from fresh Ubuntu 24.04)
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | bash

# Cloud-init bootstrap (reboots VPS, runs install post-reboot)
curl -fsSL .../bootstrap.sh | bash -s -- --mgmt-key <key>

# Status check
MGMT_KEY=$(grep ^HERMES_MGMT_API_KEY /opt/hermes/.env | cut -d= -f2)
curl -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/status

# Restart all
systemctl restart hermes.target
# Or: curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/restart

# Change domain (requires DNS pointing to VPS)
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"domain":"new.example.com"}' http://localhost:9997/api/domain
```

## Management API layout

```
hermes_mgmt/
├── main.py              FastAPI app factory, CORS, middleware, router mount
├── config.py            pydantic-settings reading /opt/hermes/.env
├── auth.py              bcrypt + HMAC session tokens + Bearer compare
├── deps.py              FastAPI deps: require_auth, rate limiter
├── models.py            Pydantic schemas for all 42 endpoints
├── env_file.py          Atomic .env read/write + masking
├── systemd_ctl.py       Async subprocess wrappers for systemctl + journalctl
├── cli_runner.py        Async `hermes` CLI runner with whitelist
├── hermes_fs.py         Read Hermes config.yaml + log files
└── routes/
    ├── status.py        /api/info, /api/status, /api/version, /api/system, /api/domain
    ├── control.py       /api/restart, /api/stop, /api/start, /api/rebuild, /api/upgrade, /api/reset
    ├── config_routes.py /api/config, /api/providers, /api/config/provider, /api/config/api-key, /api/config/test-key
    ├── channels.py      /api/channels (GET/PUT/DELETE per channel)
    ├── cron_routes.py   /api/cron (wraps hermes cron *)
    ├── logs.py          /api/logs, /api/logs/stream (SSE), /api/logs/files
    ├── auth_routes.py   /login, /api/auth/* (login, create-user, change-password, etc.)
    ├── env_routes.py    /api/env, /api/env/{key} (PUT/DELETE)
    └── cli_routes.py    POST /api/cli — run whitelisted subcommand
```

Response envelope: `ApiResponse(ok: bool, data: Any | None, error: str | None)`.

## Security posture

- UFW: 22 (limit), 80, 443, 9997 allowed; all else denied
- Tokens: 64-char hex via `openssl rand -hex 32`
- bcrypt cost 12 for password hashing
- Rate limit: 10 failures / 15 min / IP → 429
- Key masking: any env key matching `(?i)(_KEY|_TOKEN|_SECRET|_PASSWORD|_HASH)$` → `sk-****<last4>`
- HSTS + strict CORS (explicit origin allowlist)
- fail2ban: SSH only (mgmt-api rate limit is in-process)

## Test invariants (pytest)

- 63 tests under `management-api/tests/` — must all pass before release
- No real subprocess calls in tests — everything mocked
- `fastapi.testclient.TestClient` for HTTP tests
- Dependency injection for settings override via `app.dependency_overrides[get_settings]`

## Upgrade procedure

```bash
# Hermes (via API)
curl -X POST -H "Authorization: Bearer $MGMT_KEY" http://localhost:9997/api/upgrade
# Does: cd /opt/hermes/hermes-agent && git pull && uv pip install -e '.[extras]' && systemctl restart hermes-gateway hermes-dashboard

# Management API (re-run install with --skip-hermes)
cd /opt/hermes-mgmt && git pull 2>/dev/null || true
/opt/hermes-mgmt/.venv/bin/uv pip install --python /opt/hermes-mgmt/.venv/bin/python -e /opt/hermes-mgmt
systemctl restart hermes-mgmt
```

## Rollback

Each systemd unit has `Restart=always` with exponential backoff. On a bad upgrade:

```bash
cd /opt/hermes/hermes-agent
git reflog --oneline | head -10
git reset --hard <previous-sha>
/opt/hermes/hermes-agent/.venv/bin/uv pip install -e '.[web,messaging,cron,voice,mcp,honcho]'
systemctl restart hermes.target
```

## Known limitations (v0.1)

- v1 runs all services as root — dedicated `hermes` user + polkit deferred to v0.2
- `/api/self-update` not implemented — use manual upgrade above
- No device pairing flow (Hermes doesn't need it — uses `hermes login` / `hermes auth add`)
- Custom providers not API-managed — edit `/etc/hermes/config/custom-*.json` directly
- WhatsApp/Signal require additional setup outside scope (see Hermes docs)
