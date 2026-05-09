#!/bin/bash
# =============================================================================
# Hermes Agent VPS — Bare-metal one-shot installer (Ubuntu 24.04)
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/install.sh | \
#     bash -s -- [--mgmt-key <KEY>] [--domain <FQDN>]
#
#   bash install.sh [--mgmt-key <KEY>] [--domain <FQDN>] [--ref <git-ref>]
#
# Flags:
#   --mgmt-key   Pre-supplied Management API key (auto-gen if omitted)
#   --domain     FQDN for Caddy Let's Encrypt (auto-detect from hostname if omitted)
#   --ref        Hermes git ref (branch/tag/SHA, default: main)
#   --skip-hermes  Skip Hermes Agent install (useful for mgmt-api-only updates)
# =============================================================================

set -euo pipefail

# ---- Constants ------------------------------------------------------------
readonly APP_NAME="hermes-vps"
readonly APP_VERSION="0.1.0"
readonly MGMT_REPO_RAW="https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
readonly HERMES_REPO_URL="https://github.com/NousResearch/hermes-agent.git"
readonly INSTALL_DIR="/opt/hermes"
readonly HERMES_SRC_DIR="${INSTALL_DIR}/hermes-agent"
readonly MGMT_DIR="/opt/hermes-mgmt"
readonly TEMPLATES_DIR="/etc/hermes/config"
readonly LOG_FILE="/var/log/hermes-install.log"
readonly MGMT_API_PORT=9997
readonly DASHBOARD_PORT=9119
readonly HERMES_EXTRAS="web,messaging,cron,voice,mcp,honcho"
readonly PYTHON_PIN="3.11"

# ---- Args -----------------------------------------------------------------
MGMT_API_KEY_ARG=""
DOMAIN_ARG=""
HERMES_REF="main"
SKIP_HERMES=false
while [[ $# -gt 0 ]]; do
  case $1 in
    --mgmt-key)   MGMT_API_KEY_ARG="$2"; shift 2 ;;
    --domain)     DOMAIN_ARG="$2"; shift 2 ;;
    --ref)        HERMES_REF="$2"; shift 2 ;;
    --skip-hermes) SKIP_HERMES=true; shift ;;
    -h|--help)
      sed -n '3,14p' "$0" | sed 's/^# //' | sed 's/^#$//'
      exit 0 ;;
    *) shift ;;
  esac
done

# ---- Logging --------------------------------------------------------------
mkdir -p "$(dirname "$LOG_FILE")"
log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
die() { log "FATAL: $*"; exit 1; }
step() { log ""; log "==== $* ===="; }

log "=== hermes-vps installer ${APP_VERSION} starting ==="

# ---- 1. OS check ----------------------------------------------------------
step "1. OS check"
if [[ ! -f /etc/os-release ]]; then
  die "/etc/os-release not found"
fi
. /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "24.04" ]]; then
  die "Unsupported OS: ${PRETTY_NAME:-unknown}. Required: Ubuntu 24.04"
fi
if [[ "$(id -u)" != "0" ]]; then
  die "Must run as root"
fi
log "OS OK: ${PRETTY_NAME}"

# ---- 1b. Pre-flight: disk + RAM ------------------------------------------
DISK_AVAIL_GB=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
MEM_TOTAL_MB=$(awk '/^MemTotal:/ {print int($2/1024)}' /proc/meminfo)
log "Pre-flight: disk=${DISK_AVAIL_GB}GB free, RAM=${MEM_TOTAL_MB}MB total"
if [[ "${DISK_AVAIL_GB:-0}" -lt 6 ]]; then
  die "Insufficient disk: need >=6GB free on /, have ${DISK_AVAIL_GB}GB"
fi
if [[ "${MEM_TOTAL_MB:-0}" -lt 900 ]]; then
  die "Insufficient RAM: need >=1GB, have ${MEM_TOTAL_MB}MB"
fi

# ---- 2. Apt lock handling -------------------------------------------------
step "2. Apt lock + cloud-init wait"

systemctl stop unattended-upgrades 2>/dev/null || true
systemctl disable unattended-upgrades 2>/dev/null || true
systemctl stop apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl disable apt-daily.timer apt-daily-upgrade.timer 2>/dev/null || true
systemctl kill --kill-who=all apt-daily.service apt-daily-upgrade.service unattended-upgrades.service 2>/dev/null || true
killall -9 unattended-upgr apt apt-get dpkg 2>/dev/null || true
sleep 3

rm -f /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null || true
rm -f /var/lib/dpkg/updates/* 2>/dev/null || true
dpkg --force-confdef --force-confold --configure -a 2>/dev/null || true

is_apt_locked() {
  if command -v lsof &>/dev/null; then
    lsof /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null | grep -q .
    return $?
  fi
  fuser /var/lib/dpkg/lock-frontend /var/lib/dpkg/lock /var/lib/apt/lists/lock 2>/dev/null
}

wait_for_apt() {
  local max=180 waited=0
  while [[ $waited -lt $max ]]; do
    if ! is_apt_locked; then
      return 0
    fi
    log "apt lock held, waiting 5s (${waited}s/${max}s)..."
    sleep 5
    waited=$((waited + 5))
  done
  log "WARN: apt lock still held after ${max}s, forcing release"
  killall -9 apt apt-get dpkg unattended-upgr 2>/dev/null || true
  rm -f /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null
  dpkg --force-confdef --force-confold --configure -a 2>/dev/null || true
}

apt_retry() {
  local retries=3 i=0
  while [[ $i -lt $retries ]]; do
    wait_for_apt
    if "$@"; then return 0; fi
    i=$((i + 1))
    log "apt retry ${i}/${retries}: cleaning lock + reconfiguring dpkg..."
    killall -9 apt apt-get dpkg 2>/dev/null || true
    rm -f /var/lib/dpkg/lock /var/lib/dpkg/lock-frontend /var/lib/apt/lists/lock /var/cache/apt/archives/lock 2>/dev/null || true
    rm -f /var/lib/dpkg/updates/* 2>/dev/null || true
    dpkg --force-confdef --force-confold --configure -a 2>/dev/null || true
    sleep 5
  done
  log "FATAL: apt command failed after ${retries} attempts: $*"
  return 1
}

wait_for_apt

# ---- 3. Detect domain -----------------------------------------------------
step "3. Detect domain"
DROPLET_IP=$(hostname -I | awk '{print $1}')
if [[ -z "$DROPLET_IP" ]]; then
  DROPLET_IP=$(curl -sf --max-time 5 https://api.ipify.org 2>/dev/null || echo "127.0.0.1")
fi

if [[ -n "$DOMAIN_ARG" ]]; then
  DOMAIN="$DOMAIN_ARG"
  log "Domain (from flag): ${DOMAIN}"
else
  HOSTNAME_FQDN=$(hostname -f 2>/dev/null || hostname)
  if [[ "$HOSTNAME_FQDN" == *.* && "$HOSTNAME_FQDN" != "localhost."* ]]; then
    DOMAIN="$HOSTNAME_FQDN"
    log "Domain (from hostname -f): ${DOMAIN}"
  else
    DOMAIN="${DROPLET_IP}.sslip.io"
    log "Domain (sslip.io fallback): ${DOMAIN}"
  fi
fi

# ---- 4. DNS pre-check (non-fatal) -----------------------------------------
step "4. DNS pre-check"
DNS_READY=false

# Resolve via DoH first (1.1.1.1) — bypass /etc/hosts which often maps the VPS
# hostname to 127.0.1.1 on Ubuntu, then fall back to getent for non-public domains.
resolve_domain() {
  local d="$1" r=""
  r=$(curl -sf --max-time 5 "https://1.1.1.1/dns-query?name=${d}&type=A" \
      -H "accept: application/dns-json" 2>/dev/null \
    | grep -oE '"data":[ ]*"[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+"' \
    | head -1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+') || true
  if [[ -z "$r" ]]; then
    r=$(getent ahostsv4 "$d" 2>/dev/null \
      | awk '$1 !~ /^127\./ && $1 !~ /^::/ {print $1; exit}')
  fi
  echo "$r"
}

# Skip DNS check entirely for sslip.io fallback (it always resolves correctly)
if [[ "$DOMAIN" == *.sslip.io ]]; then
  DNS_READY=true
  log "DNS skip: sslip.io fallback (always resolves)"
else
  for i in 1 2 3 4 5 6; do
    RESOLVED=$(resolve_domain "$DOMAIN")
    if [[ "$RESOLVED" == "$DROPLET_IP" ]]; then
      DNS_READY=true
      log "DNS OK: ${DOMAIN} -> ${DROPLET_IP}"
      break
    fi
    log "DNS wait ${i}/6: ${DOMAIN} -> ${RESOLVED:-?} (need ${DROPLET_IP})"
    sleep 5
  done
fi

if [[ "$DNS_READY" == "false" ]]; then
  log "WARN: DNS not ready; Caddy will use self-signed TLS"
fi

# ---- 5. System packages ---------------------------------------------------
step "5. Install system packages"
export DEBIAN_FRONTEND=noninteractive
apt_retry apt-get -qqy update
apt_retry apt-get -qqy -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold install \
  curl ca-certificates gnupg ufw fail2ban jq dnsutils git build-essential \
  libssl-dev libffi-dev python3-venv python3-pip ffmpeg \
  debian-keyring debian-archive-keyring apt-transport-https

# ---- 6. Install uv + Python 3.11 ------------------------------------------
step "6. Install uv + Python 3.11"
if ! command -v uv &>/dev/null; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
  ln -sf /root/.local/bin/uv /usr/local/bin/uv
fi
uv --version
uv python install "$PYTHON_PIN"

# ---- 6b. Install Node.js 22 (required for Hermes web dashboard build) -----
step "6b. Install Node.js 22"
if ! command -v node &>/dev/null || [[ "$(node -v 2>/dev/null)" != v22* ]]; then
  curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
  apt_retry apt-get -qqy install nodejs
fi
log "Node: $(node -v) / npm: $(npm -v)"

# ---- 7. Install Caddy -----------------------------------------------------
step "7. Install Caddy"
if ! command -v caddy &>/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
    | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
    > /etc/apt/sources.list.d/caddy-stable.list
  apt_retry apt-get -qqy update
  apt_retry apt-get -qqy install caddy
fi
log "Caddy: $(caddy version | head -1)"

# ---- 8. UFW (idempotent — preserves user-added rules) ---------------------
step "8. Configure UFW"
ufw_rule_exists() { ufw status 2>/dev/null | grep -qE "^${1}\b"; }

# Set defaults only if firewall not yet active (preserve operator changes)
if ! ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw default deny incoming
  ufw default allow outgoing
fi

ufw_rule_exists "80/tcp"                  || ufw allow 80/tcp  comment 'http'
ufw_rule_exists "443/tcp"                 || ufw allow 443/tcp comment 'https'
ufw_rule_exists "${MGMT_API_PORT}/tcp"    || ufw allow "${MGMT_API_PORT}/tcp" comment 'hermes-mgmt'
ufw status 2>/dev/null | grep -q "22/tcp.*LIMIT" || ufw limit ssh/tcp
ufw --force enable

# ---- 9. Install layout ----------------------------------------------------
step "9. Create install layout"
mkdir -p "${INSTALL_DIR}" "${INSTALL_DIR}/.hermes" "${INSTALL_DIR}/data"
mkdir -p "${MGMT_DIR}" "${TEMPLATES_DIR}" "${TEMPLATES_DIR}/channels"
mkdir -p /var/log/caddy

# ---- 10. Install Hermes Agent (editable via uv) ---------------------------
if [[ "$SKIP_HERMES" != "true" ]]; then
  step "10. Install Hermes Agent (ref=${HERMES_REF})"
  if [[ ! -d "${HERMES_SRC_DIR}/.git" ]]; then
    git clone "${HERMES_REPO_URL}" "${HERMES_SRC_DIR}"
  fi
  cd "${HERMES_SRC_DIR}"
  git fetch --tags origin
  git checkout "${HERMES_REF}"
  git pull --ff-only origin "${HERMES_REF}" 2>/dev/null || true

  if [[ ! -d "${HERMES_SRC_DIR}/.venv" ]]; then
    uv venv --python "$PYTHON_PIN" "${HERMES_SRC_DIR}/.venv"
  fi
  # shellcheck source=/dev/null
  VIRTUAL_ENV="${HERMES_SRC_DIR}/.venv" uv pip install --python "${HERMES_SRC_DIR}/.venv/bin/python" \
    -e ".[${HERMES_EXTRAS}]"

  ln -sf "${HERMES_SRC_DIR}/.venv/bin/hermes" /usr/local/bin/hermes
  log "Hermes: $(/usr/local/bin/hermes version 2>/dev/null | head -1 || echo 'installed')"

  # Build web dashboard ahead of time — Hermes auto-build during systemd start
  # fails (no TTY, kills child npm processes after 7s).
  if [[ -d "${HERMES_SRC_DIR}/web" && ! -d "${HERMES_SRC_DIR}/hermes_cli/web_dist" ]]; then
    log "Building Hermes web dashboard (npm install + build)..."
    pushd "${HERMES_SRC_DIR}/web" >/dev/null
    npm install --no-audit --no-fund --loglevel=error
    npm run build
    popd >/dev/null
    log "Web dashboard built"
  fi
else
  log "Skipping Hermes install (--skip-hermes)"
fi

# ---- 11. Install management-api (FastAPI) ---------------------------------
step "11. Install management-api"
cd "${MGMT_DIR}"
if [[ ! -d .git ]]; then
  if [[ -f "${MGMT_DIR}/pyproject.toml" ]]; then
    log "Mgmt API sources already present, skipping download"
  else
    log "Downloading management-api sources from ${MGMT_REPO_RAW}"
    for f in pyproject.toml \
             hermes_mgmt/__init__.py hermes_mgmt/main.py hermes_mgmt/config.py \
             hermes_mgmt/auth.py hermes_mgmt/deps.py hermes_mgmt/models.py \
             hermes_mgmt/env_file.py hermes_mgmt/systemd_ctl.py \
             hermes_mgmt/cli_runner.py hermes_mgmt/hermes_fs.py \
             hermes_mgmt/routes/__init__.py hermes_mgmt/routes/status.py \
             hermes_mgmt/routes/control.py hermes_mgmt/routes/config_routes.py \
             hermes_mgmt/routes/channels.py hermes_mgmt/routes/cron_routes.py \
             hermes_mgmt/routes/logs.py hermes_mgmt/routes/auth_routes.py \
             hermes_mgmt/routes/env_routes.py hermes_mgmt/routes/cli_routes.py; do
      mkdir -p "$(dirname "${MGMT_DIR}/${f}")"
      curl -fsSL "${MGMT_REPO_RAW}/management-api/${f}" -o "${MGMT_DIR}/${f}" \
        || die "Failed to fetch ${f}"
    done
  fi
fi

if [[ ! -d "${MGMT_DIR}/.venv" ]]; then
  uv venv --python "$PYTHON_PIN" "${MGMT_DIR}/.venv"
fi
VIRTUAL_ENV="${MGMT_DIR}/.venv" uv pip install --python "${MGMT_DIR}/.venv/bin/python" \
  -e "${MGMT_DIR}"

# ---- 12. Generate tokens + .env ------------------------------------------
step "12. Generate tokens + .env"
GATEWAY_TOKEN=$(openssl rand -hex 32)
MGMT_API_KEY="${MGMT_API_KEY_ARG:-$(openssl rand -hex 32)}"
SESSION_SECRET=$(openssl rand -hex 32)
AUTH_TOKEN=$(openssl rand -hex 24)  # Caddy dashboard gate token

if [[ "$DNS_READY" == "true" ]]; then
  CADDY_TLS_VALUE=""
else
  CADDY_TLS_VALUE="tls internal"
fi

if [[ ! -f "${INSTALL_DIR}/.env" ]]; then
  cat > "${INSTALL_DIR}/.env" <<EOF
# hermes-vps environment — written by install.sh $(date -u +%FT%TZ)
# After changes: systemctl restart hermes-gateway hermes-dashboard hermes-mgmt caddy

# --- Core ---
HERMES_HOME=${INSTALL_DIR}/.hermes
HERMES_VPS_VERSION=${APP_VERSION}
HERMES_DROPLET_IP=${DROPLET_IP}
DOMAIN=${DOMAIN}
CADDY_TLS=${CADDY_TLS_VALUE}

# --- Ports ---
HERMES_DASHBOARD_PORT=${DASHBOARD_PORT}
HERMES_MGMT_PORT=${MGMT_API_PORT}

# --- Auth ---
HERMES_GATEWAY_TOKEN=${GATEWAY_TOKEN}
HERMES_MGMT_API_KEY=${MGMT_API_KEY}
HERMES_MGMT_SESSION_SECRET=${SESSION_SECRET}
HERMES_AUTH_TOKEN=${AUTH_TOKEN}

# --- Provider API keys (fill what you use) ---
# OPENAI_API_KEY=
# ANTHROPIC_API_KEY=
# NOUS_API_KEY=
# OPENROUTER_API_KEY=
# HUGGINGFACE_TOKEN=

# --- Messaging platform tokens (fill what you use) ---
# TELEGRAM_BOT_TOKEN=
# DISCORD_BOT_TOKEN=
# SLACK_BOT_TOKEN=
# SLACK_APP_TOKEN=
# SIGNAL_ACCOUNT=
EOF
  chmod 600 "${INSTALL_DIR}/.env"
  log "Wrote ${INSTALL_DIR}/.env"
else
  log "Preserving existing .env"
  # Append tokens only if missing (do not rotate existing values)
  grep -q '^HERMES_MGMT_API_KEY=' "${INSTALL_DIR}/.env" || \
    echo "HERMES_MGMT_API_KEY=${MGMT_API_KEY}" >> "${INSTALL_DIR}/.env"
  grep -q '^HERMES_AUTH_TOKEN=' "${INSTALL_DIR}/.env" || \
    echo "HERMES_AUTH_TOKEN=${AUTH_TOKEN}" >> "${INSTALL_DIR}/.env"
  # Re-read AUTH_TOKEN from file in case we preserved an earlier value
  AUTH_TOKEN=$(grep '^HERMES_AUTH_TOKEN=' "${INSTALL_DIR}/.env" | cut -d= -f2)
fi

# ---- 13. Seed config templates -------------------------------------------
step "13. Seed provider/channel templates"
# Provider templates — keep in sync with config/*.json in this repo + with the
# _PROVIDER_BASE_URLS map in management-api/hermes_mgmt/routes/config_routes.py
for tpl in anthropic openai google xai \
           deepseek groq mistral together \
           nous-portal openrouter huggingface \
           kimi mimo minimax zai; do
  curl -fsSL "${MGMT_REPO_RAW}/config/${tpl}.json" \
    -o "${TEMPLATES_DIR}/${tpl}.json" 2>/dev/null \
    || log "WARN: template ${tpl}.json not fetched (may not exist yet)"
done
for ch in telegram discord slack signal whatsapp email; do
  curl -fsSL "${MGMT_REPO_RAW}/config/channels/${ch}.json" \
    -o "${TEMPLATES_DIR}/channels/${ch}.json" 2>/dev/null \
    || log "WARN: channel template ${ch}.json not fetched"
done

# ---- 14. Write Caddyfile -------------------------------------------------
step "14. Write Caddyfile"
cat > "${INSTALL_DIR}/Caddyfile" <<'CADDYFILE'
{
    email admin@{$DOMAIN}
    admin off
}

{$DOMAIN} {
    {$CADDY_TLS}

    header {
        Strict-Transport-Security "max-age=31536000; includeSubDomains"
        X-Content-Type-Options "nosniff"
        X-Frame-Options "SAMEORIGIN"
        Referrer-Policy "strict-origin-when-cross-origin"
        -Server
    }

    # ---- Dashboard auth gate ----
    # First-visit URL:  https://<domain>/?token=$HERMES_AUTH_TOKEN
    #   -> sets persistent cookie, strips token from history (302 to /)
    # Subsequent visits: cookie hermes_auth=<token> grants access (30 days)
    # Without either:    403 with hint
    @auth_via_query {
        query token={$HERMES_AUTH_TOKEN}
    }
    handle @auth_via_query {
        header Set-Cookie "hermes_auth={$HERMES_AUTH_TOKEN}; Path=/; Max-Age=2592000; HttpOnly; Secure; SameSite=Lax"
        redir https://{host}/ 302
    }

    @auth_via_cookie {
        header Cookie *hermes_auth={$HERMES_AUTH_TOKEN}*
    }
    handle @auth_via_cookie {
        # Hermes owns /api/* (status, sessions, env, model, providers, logs).
        # Rewrite Host so its strict Host header check passes. Management API
        # stays on its own port (9997) — its /api/* would conflict.
        reverse_proxy 127.0.0.1:9119 {
            header_up Host "localhost:9119"
            flush_interval -1
            transport http {
                read_timeout 24h
            }
        }
    }

    handle {
        respond "Forbidden. Append ?token=<HERMES_AUTH_TOKEN> to authenticate." 403
    }

    log {
        output file /var/log/caddy/access.log {
            roll_size 50mb
            roll_keep 5
        }
    }
}
CADDYFILE

# ---- 15. Write systemd units ---------------------------------------------
step "15. Write systemd units"

# Target so we can start/stop all 3 Hermes services atomically
cat > /etc/systemd/system/hermes.target <<'EOF'
[Unit]
Description=Hermes Agent target (gateway + dashboard + management API)
Wants=hermes-gateway.service hermes-dashboard.service hermes-mgmt.service
After=network-online.target

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hermes-gateway.service <<EOF
[Unit]
Description=Hermes Agent — Messaging Gateway
After=network-online.target
Wants=network-online.target
PartOf=hermes.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=HOME=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/local/bin/hermes gateway run
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
MemoryMax=1G

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hermes-dashboard.service <<EOF
[Unit]
Description=Hermes Agent — Web Dashboard (FastAPI)
After=network-online.target
Wants=network-online.target
PartOf=hermes.target

[Service]
Type=simple
User=root
WorkingDirectory=${INSTALL_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=HOME=${INSTALL_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/local/bin/hermes dashboard --no-open --host 127.0.0.1 --port ${DASHBOARD_PORT}
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/hermes-mgmt.service <<EOF
[Unit]
Description=Hermes VPS Management API (FastAPI)
After=network-online.target
Wants=network-online.target
PartOf=hermes.target

[Service]
Type=simple
User=root
WorkingDirectory=${MGMT_DIR}
EnvironmentFile=${INSTALL_DIR}/.env
Environment=PYTHONUNBUFFERED=1
Environment=HERMES_INSTALL_DIR=${INSTALL_DIR}
Environment=HERMES_TEMPLATES_DIR=${TEMPLATES_DIR}
ExecStart=${MGMT_DIR}/.venv/bin/uvicorn hermes_mgmt.main:app --host 0.0.0.0 --port ${MGMT_API_PORT} --workers 1
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
MemoryMax=512M

[Install]
WantedBy=multi-user.target
EOF

# Caddy override — use our Caddyfile + .env
mkdir -p /etc/systemd/system/caddy.service.d
cat > /etc/systemd/system/caddy.service.d/override.conf <<EOF
[Service]
EnvironmentFile=${INSTALL_DIR}/.env
ExecStart=
ExecStart=/usr/bin/caddy run --environ --config ${INSTALL_DIR}/Caddyfile --adapter caddyfile
EOF

systemctl daemon-reload

# ---- 16. Enable + start ---------------------------------------------------
step "16. Enable + start services"
systemctl enable hermes.target hermes-gateway.service hermes-dashboard.service hermes-mgmt.service caddy.service fail2ban.service
systemctl restart caddy.service
systemctl start hermes-mgmt.service
# Gateway + dashboard only start if Hermes was installed (may lack config)
if [[ "$SKIP_HERMES" != "true" ]]; then
  systemctl start hermes-dashboard.service || log "WARN: dashboard start failed (config pending)"
  systemctl start hermes-gateway.service   || log "WARN: gateway start failed (config pending — run 'hermes gateway setup')"
fi
systemctl restart fail2ban.service

# ---- 17. Health wait ------------------------------------------------------
step "17. Health wait"
for i in $(seq 1 18); do
  if curl -sf "http://127.0.0.1:${MGMT_API_PORT}/health" >/dev/null 2>&1; then
    log "mgmt-api healthy after ${i}x5s"
    break
  fi
  sleep 5
done

# ---- 18. Cleanup ----------------------------------------------------------
step "18. Cleanup"
apt-get -qqy autoremove
apt-get -qqy autoclean

# ---- 19. Print success banner --------------------------------------------
SCHEME="https"  # Caddy serves https either via Let's Encrypt or self-signed

svc_status() {
  local svc="$1"
  if systemctl is-active --quiet "$svc"; then
    echo "OK"
  else
    echo "FAIL ($(systemctl is-active "$svc" 2>&1))"
  fi
}

log ""
log "============================================================"
log "  hermes-vps install complete"
log "============================================================"
log ""
log "  Service status:"
log "    caddy             : $(svc_status caddy.service)"
log "    hermes-mgmt       : $(svc_status hermes-mgmt.service)"
log "    hermes-gateway    : $(svc_status hermes-gateway.service)"
log "    hermes-dashboard  : $(svc_status hermes-dashboard.service)"
log "    fail2ban          : $(svc_status fail2ban.service)"
log ""
log "  Dashboard URL (first visit, sets a 30d cookie then strips token):"
log "    ${SCHEME}://${DOMAIN}/?token=${AUTH_TOKEN}"
log ""
log "  Management API: http://${DROPLET_IP}:${MGMT_API_PORT}  (port-only, not proxied via Caddy)"
log "  TLS mode:       $([[ "$DNS_READY" == "true" ]] && echo "Let's Encrypt" || echo "self-signed (DNS not ready)")"
log ""
log "  AUTH_TOKEN:     ${AUTH_TOKEN}        (dashboard gate)"
log "  MGMT_API_KEY:   ${MGMT_API_KEY}        (mgmt API bearer)"
log "  GATEWAY_TOKEN:  ${GATEWAY_TOKEN}        (hermes gateway)"
log ""
log "  Next steps:"
log "    hermes gateway setup             # configure messaging channels"
log "    hermes model                     # pick provider + model"
log "    hermes config show               # verify config"
log "    systemctl status hermes-gateway  # inspect services"
log "    journalctl -u hermes-gateway -f  # follow logs"
log ""
log "  Quick health check:"
log "    curl -H 'Authorization: Bearer ${MGMT_API_KEY}' \\"
log "         http://127.0.0.1:${MGMT_API_PORT}/api/status"
log ""
log "============================================================"
