# Deploy Guide — hermes-vps

Step-by-step from a fresh Ubuntu 24.04 VPS to running Hermes Agent.

## Prerequisites

- Ubuntu 24.04 x86_64 VPS (1 vCPU / 2 GB RAM minimum; 4 GB recommended for voice/STT)
- Root SSH access
- (Optional) A domain with DNS A record pointing to the VPS IP — enables real Let's Encrypt SSL
- Provider API key for at least one LLM (Anthropic / OpenAI / Nous Portal / OpenRouter / etc.)

## Option 1: Direct install (recommended for small deployments)

```bash
ssh root@<VPS_IP>
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | bash
```

Flow (~6-8 min on 1 vCPU / 2 GB):
1. OS check (abort if not Ubuntu 24.04)
2. Wait for apt lock
3. Detect domain from `hostname -f` (fallback `<IP>.sslip.io`)
4. DNS pre-check (30s wait, fallback to self-signed TLS)
5. Install system packages: curl, jq, ufw, fail2ban, Caddy, ffmpeg, git, build tools
6. Install `uv` + Python 3.11
7. Install Caddy from cloudsmith
8. Configure UFW (allow 22 limit, 80, 443, 9997)
9. Create `/opt/hermes/` + `/opt/hermes-mgmt/` + `/etc/hermes/config/`
10. Git clone Hermes → `uv venv` → `uv pip install -e '.[web,messaging,cron,voice,mcp,honcho]'`
11. Download + install `management-api` via `uv pip install -e`
12. Generate tokens → write `.env`
13. Seed provider + channel config templates
14. Write Caddyfile + 3 systemd unit files + caddy override
15. Enable + start `hermes.target` + caddy + fail2ban
16. Health wait (curl :9997/health)
17. Print dashboard URL + MGMT_API_KEY

At the end you'll see:

```
============================================================
  hermes-vps install complete
============================================================

  Dashboard:      https://<HOSTNAME>/
  Management API: https://<HOSTNAME>/api
  MGMT_API_KEY:   xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
  GATEWAY_TOKEN:  xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

  Next steps:
    hermes gateway setup             # configure messaging channels
    hermes model                     # pick provider + model
    hermes config show               # verify config
    systemctl status hermes-gateway  # inspect services
    journalctl -u hermes-gateway -f  # follow logs
```

**Save `MGMT_API_KEY` immediately** — you need it to call the REST API.

## Option 2: Cloud-init bootstrap (reboot + install)

For providers that run long cloud-init hooks (DigitalOcean, Linode, Vultr):

```bash
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/bootstrap.sh | bash
```

This waits for cloud-init to finish, schedules `install.sh` via a systemd one-shot, then reboots. Post-reboot, install runs automatically. Monitor with:

```bash
journalctl -u hermes-install -f
tail -f /var/log/hermes-install.log
```

## First-time configuration

After install finishes, SSH into the VPS and configure Hermes interactively:

```bash
# Pick LLM provider + model
hermes model

# Walk through messaging channels (Telegram bot token, Discord, etc.)
hermes gateway setup

# Verify
hermes config show
hermes doctor
```

Alternatively, configure via REST API from your laptop:

```bash
MGMT_KEY=<your-key>
VPS=https://<your-hostname>

# Pick Anthropic Claude Sonnet
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"anthropic","model":"anthropic/claude-sonnet-4-6"}' \
  $VPS/api/config/provider

# Set API key
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"anthropic","api_key":"sk-ant-..."}' \
  $VPS/api/config/api-key

# Test the key
curl -X POST -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"provider":"anthropic","api_key":"sk-ant-..."}' \
  $VPS/api/config/test-key

# Enable Telegram
curl -X PUT -H "Authorization: Bearer $MGMT_KEY" -H "Content-Type: application/json" \
  -d '{"token":"123456:ABC-..."}' \
  $VPS/api/channels/telegram

# Restart to pick up changes
curl -X POST -H "Authorization: Bearer $MGMT_KEY" $VPS/api/restart
```

## Verify

```bash
# From the VPS
systemctl is-active hermes-gateway hermes-dashboard hermes-mgmt caddy
# Expected: active (all 4)

# From your laptop
curl -H "Authorization: Bearer $MGMT_KEY" $VPS/api/status
# Expected: {"ok":true,"data":{"services":[{"name":"hermes-gateway","active":true,...}]}}

# Open dashboard in browser
open $VPS/
```

## Upgrade Hermes Agent

```bash
# Via API (recommended)
curl -X POST -H "Authorization: Bearer $MGMT_KEY" $VPS/api/upgrade

# Or manually
cd /opt/hermes/hermes-agent
git pull
/opt/hermes/hermes-agent/.venv/bin/uv pip install --python /opt/hermes/hermes-agent/.venv/bin/python -e '.[web,messaging,cron,voice,mcp,honcho]'
systemctl restart hermes-gateway hermes-dashboard
```

## Upgrade management-api

```bash
# Re-run install.sh with --skip-hermes
curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | \
  bash -s -- --skip-hermes
```

## Uninstall

```bash
systemctl stop hermes.target caddy
systemctl disable hermes.target hermes-gateway hermes-dashboard hermes-mgmt
rm -f /etc/systemd/system/hermes*.service /etc/systemd/system/hermes.target
rm -rf /etc/systemd/system/caddy.service.d
systemctl daemon-reload
rm -rf /opt/hermes /opt/hermes-mgmt /etc/hermes
apt-get -y remove caddy
ufw reset
```

## Troubleshooting

### `install.sh` aborts with "Unsupported OS"
Only Ubuntu 24.04 is supported. Check `cat /etc/os-release`.

### Services won't start after install
```bash
journalctl -u hermes-gateway --no-pager -n 50
journalctl -u hermes-mgmt --no-pager -n 50
```

Common causes:
- No provider configured → run `hermes model` and `hermes config show`
- No API key in `.env` → `curl -X PUT ... /api/config/api-key` or edit `.env` directly
- Python venv corrupt → `rm -rf /opt/hermes/hermes-agent/.venv && bash /opt/hermes/hermes-install.sh --skip-hermes` (regenerates)

### SSL certificate fails
```bash
journalctl -u caddy --no-pager -n 50
dig +short $(grep ^DOMAIN /opt/hermes/.env | cut -d= -f2)
# Ensure DNS A record -> VPS IP, then:
systemctl restart caddy
```

If DNS isn't set, Caddy will use a self-signed cert (browser warning but functional).

### Management API returns 401
- Check `Authorization: Bearer <key>` header format
- Key is in `/opt/hermes/.env` → `HERMES_MGMT_API_KEY=...`
- No session cookie? Try `POST /api/auth/login` first (if login user configured)

### Rate-limit blocking login
```bash
# In-memory counter resets on restart:
systemctl restart hermes-mgmt
```

### Change Hermes extras (add `slack`, `matrix`, etc.)
```bash
cd /opt/hermes/hermes-agent
/opt/hermes/hermes-agent/.venv/bin/uv pip install --python /opt/hermes/hermes-agent/.venv/bin/python -e '.[web,messaging,cron,voice,mcp,honcho,slack,matrix]'
systemctl restart hermes-gateway
```
