#!/bin/bash
# =============================================================================
# hermes-vps Bootstrap — waits for cloud-init, schedules install.sh post-reboot
#
# Usage (from local shell):
#   ssh root@vps "curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/bootstrap.sh | \
#     bash -s -- --mgmt-key <KEY> [--domain <FQDN>]"
#
# Flow:
#   1. cloud-init status --wait
#   2. Download install.sh -> /opt/hermes/hermes-install.sh
#   3. Save args to /opt/hermes/hermes-install.args
#   4. Create systemd one-shot service, enable
#   5. Reboot — install.sh runs after boot
#   6. On success, service self-disables + cleans up
#
# Tail progress: journalctl -u hermes-install -f
#             or tail -f /var/log/hermes-install.log
# =============================================================================

set -euo pipefail

readonly REPO_RAW="https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
readonly BOOT_DIR="/opt/hermes"
readonly INSTALL_SCRIPT="${BOOT_DIR}/hermes-install.sh"
readonly INSTALL_ARGS="${BOOT_DIR}/hermes-install.args"
readonly LOG_FILE="/var/log/hermes-install.log"
readonly SERVICE_NAME="hermes-install"

mkdir -p "$BOOT_DIR"
echo "$*" > "$INSTALL_ARGS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] bootstrap: $*" | tee -a "$LOG_FILE" >&2; }

log "=== hermes-vps bootstrap starting ==="

# ---- 1. Wait for cloud-init ----
if command -v cloud-init &>/dev/null; then
    log "Waiting for cloud-init to finish..."
    cloud-init status --wait 2>&1 | while IFS= read -r line; do
        log "cloud-init: $line"
    done
    log "cloud-init done."
else
    log "cloud-init not present, skipping wait."
fi

# ---- 2. Download install.sh ----
log "Downloading install.sh from ${REPO_RAW}"
if ! curl -fsSL "${REPO_RAW}/install.sh" -o "$INSTALL_SCRIPT"; then
    log "FATAL: Failed to download install.sh"
    exit 1
fi
chmod +x "$INSTALL_SCRIPT"
log "install.sh downloaded ($(wc -l < "$INSTALL_SCRIPT") lines)"

# ---- 3. Write systemd one-shot ----
log "Creating ${SERVICE_NAME}.service"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=hermes-vps one-shot installer (runs once after reboot)
After=network-online.target
Wants=network-online.target
ConditionPathExists=${INSTALL_SCRIPT}

[Service]
Type=oneshot
RemainAfterExit=no
Environment=DEBIAN_FRONTEND=noninteractive
ExecStart=/bin/bash -c '${INSTALL_SCRIPT} \$(cat ${INSTALL_ARGS}) >> ${LOG_FILE} 2>&1; rc=\$?; systemctl disable ${SERVICE_NAME}.service; rm -f /etc/systemd/system/${SERVICE_NAME}.service ${INSTALL_SCRIPT} ${INSTALL_ARGS}; systemctl daemon-reload; exit \$rc'
StandardOutput=journal
StandardError=journal
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

log "Scheduled. Rebooting in 5s..."
log "Monitor with: journalctl -u ${SERVICE_NAME} -f"
log "       or:   tail -f ${LOG_FILE}"

# ---- 4. Reboot ----
nohup bash -c 'sleep 5 && systemctl reboot' >/dev/null 2>&1 &
exit 0
