#!/bin/bash
# =============================================================================
# upgrade-zalo-plugin.sh — Pull latest hermes-zalo-plugin + fix sessions mapping
#
# Use for OLD VPSes whose mgmt-api doesn't yet refresh the Zalo plugin via
# POST /api/upgrade-mgmt. Idempotent — safe to re-run any time.
#
# What it does:
#   1. Remap sessions store (owner-gate fix for old installs):
#        - append HERMES_HOME=/root/.hermes to /opt/hermes/.env if missing
#        - symlink /opt/data/sessions -> /root/.hermes/sessions if absent
#      Why: the plugin adapter reads ${HERMES_HOME}/sessions/sessions.json and
#      defaulted to /opt/data when unset, while the gateway (HOME=/root) writes
#      sessions to /root/.hermes/sessions. Mismatch => fail-closed owner-gate
#      denies EVERY zalo_* tool, even for the configured owner.
#   2. git stash (drop runtime hand-edits) + git pull --ff-only the plugin
#   3. npm install sidecar deps (zca-js)
#   4. restart hermes-gateway + verify sidecar /health
#
# Usage:
#   ssh root@<VPS> 'curl -fsSL \
#     https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/scripts/upgrade-zalo-plugin.sh \
#     | bash'
# =============================================================================

set -euo pipefail

readonly PLUGIN_DIR="/root/.hermes/plugins/zalo-personal"
readonly ENV_FILE="/opt/hermes/.env"
readonly REAL_SESSIONS="/root/.hermes/sessions"
readonly OPT_SESSIONS="/opt/data/sessions"

log() { echo "[upgrade-zalo] $*"; }

# --- 1. Remap sessions store (owner-gate fix) -------------------------------
if [[ -f "$ENV_FILE" ]] && ! grep -q '^HERMES_HOME=' "$ENV_FILE"; then
  echo "HERMES_HOME=/root/.hermes" >> "$ENV_FILE"
  log "appended HERMES_HOME=/root/.hermes to $ENV_FILE"
else
  log "HERMES_HOME already set (or no $ENV_FILE) — skip"
fi

mkdir -p "$REAL_SESSIONS" /opt/data
if [[ ! -e "$OPT_SESSIONS" && ! -L "$OPT_SESSIONS" ]]; then
  ln -s "$REAL_SESSIONS" "$OPT_SESSIONS"
  log "symlinked $OPT_SESSIONS -> $REAL_SESSIONS"
else
  log "$OPT_SESSIONS already exists — skip symlink"
fi

# --- 2. Update plugin sources ------------------------------------------------
if [[ ! -d "$PLUGIN_DIR/.git" ]]; then
  log "FATAL: $PLUGIN_DIR is not a git checkout — is the Zalo plugin installed?"
  exit 1
fi
# Drop runtime hand-edits (e.g. hotfixes the on-VPS agent applied) so
# --ff-only never fails on a dirty tree; upstream is canonical.
git -C "$PLUGIN_DIR" stash >/dev/null 2>&1 || true
git -C "$PLUGIN_DIR" pull --ff-only
log "plugin now at: $(git -C "$PLUGIN_DIR" log --oneline -1)"

# --- 3. Sidecar deps ----------------------------------------------------------
if [[ -f "$PLUGIN_DIR/sidecar/package.json" ]]; then
  log "npm install sidecar deps..."
  (cd "$PLUGIN_DIR/sidecar" && npm install --no-audit --no-fund --loglevel=error) \
    || log "WARN: npm install failed — sidecar may still run on existing deps"
fi

# --- 4. Restart gateway + verify ---------------------------------------------
log "restarting hermes-gateway..."
systemctl restart hermes-gateway
sleep 12

if systemctl is-active --quiet hermes-gateway; then
  log "gateway: active"
else
  log "WARN: gateway not active — check: journalctl -u hermes-gateway -n 50"
fi

SIDECAR_PORT="$(grep -E '^ZALO_PERSONAL_SIDECAR_PORT=' "$ENV_FILE" 2>/dev/null | cut -d= -f2)"
SIDECAR_PORT="${SIDECAR_PORT:-3838}"
HEALTH="$(curl -s --max-time 5 "http://127.0.0.1:${SIDECAR_PORT}/health" || true)"
if [[ -n "$HEALTH" ]]; then
  log "sidecar /health: $HEALTH"
else
  log "sidecar not responding yet (it may still be starting / needs QR login)"
fi

log "DONE."
