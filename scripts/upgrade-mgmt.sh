#!/bin/bash
# =============================================================================
# upgrade-mgmt.sh — bootstrap the management-api upgrade endpoint
#
# Run this ONCE per VPS, the first time you need to ship a new mgmt-api version
# to an install that doesn't yet expose POST /api/upgrade-mgmt. After that, use
#   curl -X POST -H "Authorization: Bearer $MGMT_KEY" \
#        http://<VPS-IP>:9997/api/upgrade-mgmt
# for all subsequent upgrades.
#
# Usage:
#   ssh root@<VPS> 'curl -fsSL \
#     https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/scripts/upgrade-mgmt.sh \
#     | bash'
# =============================================================================

set -euo pipefail

readonly REPO_RAW="https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
readonly MGMT_DIR="/opt/hermes-mgmt"
readonly UV_BIN="${MGMT_DIR}/.venv/bin/uv"

log() { echo "[upgrade-mgmt] $*"; }

[[ -d "$MGMT_DIR" ]] || { log "FATAL: $MGMT_DIR not found — is hermes-vps installed?"; exit 1; }

# Files mirrored from management-api/. Keep in sync with control.py::_MGMT_FILES.
files=(
  "pyproject.toml"
  "hermes_mgmt/__init__.py"
  "hermes_mgmt/main.py"
  "hermes_mgmt/config.py"
  "hermes_mgmt/auth.py"
  "hermes_mgmt/deps.py"
  "hermes_mgmt/models.py"
  "hermes_mgmt/env_file.py"
  "hermes_mgmt/systemd_ctl.py"
  "hermes_mgmt/cli_runner.py"
  "hermes_mgmt/hermes_fs.py"
  "hermes_mgmt/routes/__init__.py"
  "hermes_mgmt/routes/status.py"
  "hermes_mgmt/routes/control.py"
  "hermes_mgmt/routes/config_routes.py"
  "hermes_mgmt/routes/channels.py"
  "hermes_mgmt/routes/cron_routes.py"
  "hermes_mgmt/routes/logs.py"
  "hermes_mgmt/routes/auth_routes.py"
  "hermes_mgmt/routes/env_routes.py"
  "hermes_mgmt/routes/cli_routes.py"
)

log "Pulling ${#files[@]} files from raw URL..."
cd "$MGMT_DIR"
for f in "${files[@]}"; do
  mkdir -p "$(dirname "$f")"
  curl -fsSL "${REPO_RAW}/management-api/${f}" -o "$f" || {
    log "WARN: fetch failed for $f"
    continue
  }
done
log "Done."

# Locate uv: prefer the venv copy, fall back to PATH.
if [[ ! -x "$UV_BIN" ]]; then
  if command -v uv >/dev/null 2>&1; then
    UV="$(command -v uv)"
  else
    log "FATAL: uv not found — install with: curl -LsSf https://astral.sh/uv/install.sh | sh"
    exit 1
  fi
else
  UV="$UV_BIN"
fi
log "Using uv: $UV"

# Reinstall the package in editable mode so the new code lands in the venv.
log "Reinstalling hermes-mgmt..."
if [[ -d "${MGMT_DIR}/.venv" ]]; then
  "$UV" pip install --python "${MGMT_DIR}/.venv/bin/python" -e "$MGMT_DIR"
else
  "$UV" pip install -e "$MGMT_DIR"
fi

# Cycle the unit so the new code loads. systemd's Restart=always brings it back.
log "Restarting hermes-mgmt.service..."
systemctl restart hermes-mgmt

# Quick health probe (the service may take a second to bind).
for i in 1 2 3 4 5; do
  if curl -fsS --max-time 3 http://127.0.0.1:9997/health >/dev/null 2>&1; then
    log "OK — hermes-mgmt is healthy."
    exit 0
  fi
  sleep 1
done

log "WARN: hermes-mgmt did not respond on /health in 5s. Check 'journalctl -u hermes-mgmt'."
exit 1
