#!/bin/bash
# =============================================================================
# OpenViking — on-demand installer for hermes-vps
#
# OpenViking (github.com/volcengine/OpenViking) is an optional context database
# / memory backend for Hermes. It is NOT installed by default — the dashboard
# (or an admin) triggers this script when a user opts in.
#
# What it does:
#   1. Create an isolated uv venv at /opt/hermes-openviking
#   2. pip install openviking (provides the `openviking-server` binary)
#   3. Seed ~/.openviking/ov.conf (embedding + VLM) — reuse the user's existing
#      OpenAI key from /opt/hermes/.env when present, so low-tech users don't
#      re-enter anything
#   4. `openviking-server init` (first-time data setup)
#   5. Write the hermes-openviking.service systemd unit (does NOT start it —
#      the API's /enable step starts it + wires OPENVIKING_ENDPOINT into Hermes)
#
# Idempotent: safe to re-run. Exit non-zero on failure (the API surfaces it).
# =============================================================================
set -euo pipefail

readonly OV_DIR="/opt/hermes-openviking"
readonly OV_HOME="/root/.openviking"          # config + data (server runs as root)
readonly OV_CONF="${OV_HOME}/ov.conf"
readonly HERMES_ENV="/opt/hermes/.env"
readonly PYTHON_PIN="3.11"
readonly OV_PORT="${OV_PORT:-1933}"
readonly LOG_FILE="/var/log/hermes-openviking-install.log"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }
die() { log "FATAL: $*"; exit 1; }

mkdir -p "$(dirname "$LOG_FILE")"
log "=== OpenViking installer starting ==="

command -v uv >/dev/null 2>&1 || die "uv not found — run the main hermes install first"

# Read a key from /opt/hermes/.env (empty if absent).
read_env_value() {
  local key="$1"
  [[ -f "$HERMES_ENV" ]] || { echo ""; return; }
  grep -E "^${key}=" "$HERMES_ENV" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"' || echo ""
}

# ---- 1. venv + install ----------------------------------------------------
log "1. Creating venv at ${OV_DIR}"
mkdir -p "$OV_DIR"
if [[ ! -d "${OV_DIR}/.venv" ]]; then
  uv venv --python "$PYTHON_PIN" "${OV_DIR}/.venv"
fi

log "2. Installing openviking (pip)"
VIRTUAL_ENV="${OV_DIR}/.venv" uv pip install --python "${OV_DIR}/.venv/bin/python" \
  openviking || die "pip install openviking failed"

OV_BIN="${OV_DIR}/.venv/bin/openviking-server"
[[ -x "$OV_BIN" ]] || die "openviking-server binary missing after install"
log "   openviking-server installed: ${OV_BIN}"

# ---- 3. Seed config -------------------------------------------------------
# Reuse an OpenAI-compatible key the user already configured for Hermes. If none
# exists, write a template with empty keys — the API's /config step (or the
# dashboard) fills them in before /enable. doctor will flag missing keys.
mkdir -p "$OV_HOME"
if [[ ! -f "$OV_CONF" ]]; then
  OPENAI_KEY="$(read_env_value OPENAI_API_KEY)"
  OPENAI_BASE="$(read_env_value OPENAI_BASE_URL)"
  [[ -n "$OPENAI_BASE" ]] || OPENAI_BASE="https://api.openai.com/v1"
  log "3. Writing ${OV_CONF} (openai key: $([[ -n "$OPENAI_KEY" ]] && echo present || echo empty))"
  cat > "$OV_CONF" <<EOF
{
  "embedding": {
    "dense": {
      "api_base": "${OPENAI_BASE}",
      "api_key": "${OPENAI_KEY}",
      "provider": "openai",
      "dimension": 1536,
      "model": "text-embedding-3-small",
      "input": "multimodal"
    }
  },
  "vlm": {
    "api_base": "${OPENAI_BASE}",
    "api_key": "${OPENAI_KEY}",
    "provider": "openai",
    "max_retries": 2,
    "model": "gpt-4o-mini"
  }
}
EOF
  chmod 600 "$OV_CONF"
else
  log "3. ${OV_CONF} already exists — preserving"
fi

# ---- 4. init data ---------------------------------------------------------
log "4. openviking-server init"
HOME="/root" "$OV_BIN" init 2>&1 | tee -a "$LOG_FILE" || log "WARN: init returned non-zero (may already be initialized)"

# ---- 5. systemd unit (not started here) -----------------------------------
log "5. Writing /etc/systemd/system/hermes-openviking.service"
cat > /etc/systemd/system/hermes-openviking.service <<EOF
[Unit]
Description=Hermes Agent — OpenViking context database (optional memory backend)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
Environment=HOME=/root
Environment=PYTHONUNBUFFERED=1
WorkingDirectory=${OV_HOME}
ExecStart=${OV_BIN} --config ${OV_CONF} --port ${OV_PORT}
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal
MemoryMax=2G

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
log "=== OpenViking installed (not started). Use the dashboard /enable to start. ==="
