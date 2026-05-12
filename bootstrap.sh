#!/bin/bash
# =============================================================================
# hermes-vps Bootstrap — waits for cloud-init, schedules install.sh post-reboot
#
# Usage (from local shell):
#   ssh root@vps "curl -fsSL https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/bootstrap.sh | \
#     bash -s -- [--mgmt-key <KEY>] [--gateway-token <KEY>] [--auth-token <KEY>] [--domain <FQDN>]"
#
# Recognized flags (any pre-seeded value lands in /opt/hermes/.env so install.sh
# never rotates it):
#   --mgmt-key       -> HERMES_MGMT_API_KEY   (mgmt API bearer)
#   --gateway-token  -> HERMES_GATEWAY_TOKEN  (hermes gateway)
#   --auth-token     -> HERMES_AUTH_TOKEN     (Caddy dashboard gate)
#   --domain         -> DOMAIN                (Caddy site address)
# Unknown flags are passed through to install.sh unchanged.
#
# Flow:
#   1. cloud-init status --wait
#   2. Pre-seed /opt/hermes/.env with operator-supplied tokens
#   3. Download install.sh -> /opt/hermes/hermes-install.sh
#   4. Save args to /opt/hermes/hermes-install.args
#   5. Create systemd one-shot service, enable
#   6. Reboot — install.sh runs after boot, reads tokens from .env
#   7. On success, service self-disables + cleans up
#
# Tail progress: journalctl -u hermes-install -f
#             or tail -f /var/log/hermes-install.log
# =============================================================================

set -euo pipefail

# ---- Detach from invoking SSH session ------------------------------------
# Hostbill / cron / any phpseclib-style caller invokes us with a short SSH
# timeout (e.g. setTimeout(30)). cloud-init's --wait can take 1-3 minutes on
# a fresh VPS, so the SSH session is torn down mid-run and the child bash
# receives SIGHUP. To survive, on the first entry we re-spawn ourselves with
# setsid + nohup, redirect output to the install log, and exit immediately
# so the caller sees a clean exit 0 within milliseconds.
readonly LOG_FILE="/var/log/hermes-install.log"
if [[ "${HERMES_BOOTSTRAP_DETACHED:-0}" != "1" ]]; then
    mkdir -p /opt/hermes
    mkdir -p "$(dirname "$LOG_FILE")"
    # Persist the script on disk so setsid can re-exec it (we may be running
    # under `curl … | bash`, in which case $0 is just "bash").
    self="/opt/hermes/.bootstrap.sh"
    if [[ -f "$0" && "$0" != "bash" && "$0" != "-bash" ]]; then
        cp -f "$0" "$self"
    else
        # Re-fetch from the same raw URL we'd otherwise serve install.sh from.
        curl -fsSL "https://raw.githubusercontent.com/tinovn/vps-hermes-management/main/bootstrap.sh" \
            -o "$self" || { echo "FATAL: bootstrap self-download failed" >&2; exit 1; }
    fi
    chmod +x "$self"
    HERMES_BOOTSTRAP_DETACHED=1 setsid nohup bash "$self" "$@" \
        >>"$LOG_FILE" 2>&1 </dev/null &
    disown 2>/dev/null || true
    echo "hermes-bootstrap: detached (pid $!). Tail $LOG_FILE for progress."
    exit 0
fi

readonly REPO_RAW="https://raw.githubusercontent.com/tinovn/vps-hermes-management/main"
readonly BOOT_DIR="/opt/hermes"
readonly INSTALL_SCRIPT="${BOOT_DIR}/hermes-install.sh"
readonly INSTALL_ARGS="${BOOT_DIR}/hermes-install.args"
readonly ENV_FILE="${BOOT_DIR}/.env"
# LOG_FILE already declared above (before detach) so it survives re-exec.
readonly SERVICE_NAME="hermes-install"

mkdir -p "$BOOT_DIR"
echo "$*" > "$INSTALL_ARGS"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] bootstrap: $*" | tee -a "$LOG_FILE" >&2; }

log "=== hermes-vps bootstrap starting ==="

# ---- 0. Pre-seed .env with operator-supplied tokens ----
# Bootstrap is the initial-provisioning entry point (Hostbill / cloud-init / SSH
# one-shot). It OVERWRITES whatever was in .env so the operator's flags always
# win on first boot. install.sh then reads these values back without rotating.
write_env_key() {
  local key="$1" value="$2"
  [[ -z "$value" ]] && return 0
  touch "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
    # Use a delimiter that cannot appear in our hex tokens / FQDNs.
    sed -i.bak "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
    rm -f "${ENV_FILE}.bak"
    log "  ${key}: overwritten in .env"
  else
    echo "${key}=${value}" >> "$ENV_FILE"
    log "  ${key}: appended to .env"
  fi
}

# Parse known flags (mirrors install.sh). Unknown flags pass through unchanged
# in $INSTALL_ARGS for install.sh to handle.
prev=""
for arg in "$@"; do
  case "$prev" in
    --mgmt-key)      write_env_key HERMES_MGMT_API_KEY  "$arg" ;;
    --gateway-token) write_env_key HERMES_GATEWAY_TOKEN "$arg" ;;
    --auth-token)    write_env_key HERMES_AUTH_TOKEN    "$arg" ;;
    --domain)        write_env_key DOMAIN               "$arg" ;;
  esac
  prev="$arg"
done

# ---- 1. Wait for cloud-init ----
# cloud-init may finish with exit code 2 (RECOVERABLE_ERROR / warnings) even
# though everything succeeded; under `set -euo pipefail` that would kill the
# bootstrap silently. Capture status separately and treat non-fatal codes (0/2)
# as success. https://github.com/canonical/cloud-init/issues/4439
if command -v cloud-init &>/dev/null; then
    log "Waiting for cloud-init to finish..."
    set +e
    cloud-init status --wait >/tmp/cloud-init-wait.log 2>&1
    ci_rc=$?
    set -e
    while IFS= read -r line; do log "cloud-init: $line"; done < /tmp/cloud-init-wait.log
    rm -f /tmp/cloud-init-wait.log
    case "$ci_rc" in
        0|2) log "cloud-init done (exit=$ci_rc)." ;;
        *)   log "WARN: cloud-init exited $ci_rc; continuing anyway." ;;
    esac
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
