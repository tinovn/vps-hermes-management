#!/bin/bash
# =============================================================================
# OpenViking — uninstaller for hermes-vps
#
# Stops + removes the OpenViking service and venv. Triggered by the dashboard
# /api/openviking/uninstall. Does NOT touch Hermes config — the API's /disable
# step already removed OPENVIKING_ENDPOINT from .env before this runs.
#
# By default keeps the config + data (~/.openviking) so a re-install resumes.
# Pass --purge to also delete config + data.
# =============================================================================
set -euo pipefail

readonly OV_DIR="/opt/hermes-openviking"
readonly OV_HOME="/root/.openviking"
readonly LOG_FILE="/var/log/hermes-openviking-install.log"

PURGE=false
[[ "${1:-}" == "--purge" ]] && PURGE=true

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG_FILE" >&2; }

mkdir -p "$(dirname "$LOG_FILE")"
log "=== OpenViking uninstall starting (purge=${PURGE}) ==="

systemctl stop hermes-openviking.service 2>/dev/null || true
systemctl disable hermes-openviking.service 2>/dev/null || true
rm -f /etc/systemd/system/hermes-openviking.service
systemctl daemon-reload

rm -rf "$OV_DIR"
log "Removed venv ${OV_DIR}"

if [[ "$PURGE" == "true" ]]; then
  rm -rf "$OV_HOME"
  log "Purged config + data ${OV_HOME}"
else
  log "Kept config + data ${OV_HOME} (re-install will resume). Use --purge to delete."
fi

log "=== OpenViking uninstalled ==="
