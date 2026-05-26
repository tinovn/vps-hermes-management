#!/bin/bash
# =============================================================================
# migrate-hermes-home-to-root.sh — Unify HERMES_HOME at /root/.hermes
#
# Why: install.sh originally put HERMES_HOME at /opt/hermes/.hermes by setting
# `Environment=HOME=/opt/hermes` on the gateway/dashboard units. The Hermes
# CLI defaults to ~/.hermes (= /root/.hermes for root), and any subcommand that
# auto-installs a systemd unit (like `hermes gateway setup`) rewrites the unit
# without that HOME override — splitting the config store across two paths.
#
# This script merges everything onto /root/.hermes, the canonical CLI default.
# Idempotent: safe to re-run.
#
# Usage:
#   ssh root@<VPS> 'bash -s' < migrate-hermes-home-to-root.sh
# =============================================================================

set -euo pipefail

readonly OLD_HOME="/opt/hermes/.hermes"
readonly NEW_HOME="/root/.hermes"

log() { echo "[migrate] $*"; }

# --- 1. Bail if already migrated -------------------------------------------
if [[ ! -d "$OLD_HOME" ]]; then
  log "$OLD_HOME does not exist — nothing to migrate."
elif [[ "$(readlink -f "$OLD_HOME" 2>/dev/null)" == "$NEW_HOME" ]]; then
  log "$OLD_HOME already symlinks to $NEW_HOME — nothing to migrate."
  exit 0
fi

# --- 2. Stop services so files don't change mid-merge ----------------------
log "Stopping hermes.target..."
systemctl stop hermes.target 2>/dev/null || true

# --- 3. Merge OLD_HOME -> NEW_HOME -----------------------------------------
if [[ -d "$OLD_HOME" && ! -L "$OLD_HOME" ]]; then
  mkdir -p "$NEW_HOME"

  # Per-file merge: take NEW_HOME version when present, otherwise pull from
  # OLD_HOME. This means a key the user just wrote via SSH CLI to NEW_HOME
  # is kept, not clobbered by a stale OLD_HOME copy.
  log "Merging $OLD_HOME into $NEW_HOME..."
  (
    cd "$OLD_HOME"
    find . -mindepth 1 -print0 | while IFS= read -r -d '' rel; do
      dest="$NEW_HOME/${rel#./}"
      if [[ -d "$rel" && ! -L "$rel" ]]; then
        mkdir -p "$dest"
      elif [[ -e "$dest" ]]; then
        log "  skip (exists in new): ${rel#./}"
      else
        mkdir -p "$(dirname "$dest")"
        cp -a "$rel" "$dest"
        log "  merged: ${rel#./}"
      fi
    done
  )

  # Special case: .env may differ; merge KEY=VALUE lines (new wins on conflict)
  if [[ -f "$OLD_HOME/.env" && -f "$NEW_HOME/.env" ]]; then
    log "Merging .env files (NEW_HOME values win on conflict)..."
    tmp=$(mktemp)
    {
      grep -E '^[A-Z_][A-Z0-9_]*=' "$OLD_HOME/.env" 2>/dev/null || true
      grep -E '^[A-Z_][A-Z0-9_]*=' "$NEW_HOME/.env" 2>/dev/null || true
    } | awk -F= '!seen[$1]++' > "$tmp"
    # NB: awk above keeps FIRST occurrence; OLD is listed first, so NEW
    # entries that appear later are skipped. Reverse the order to make
    # NEW win:
    {
      grep -E '^[A-Z_][A-Z0-9_]*=' "$NEW_HOME/.env" 2>/dev/null || true
      grep -E '^[A-Z_][A-Z0-9_]*=' "$OLD_HOME/.env" 2>/dev/null || true
    } | awk -F= '!seen[$1]++' > "$tmp"
    chmod 600 "$tmp"
    mv "$tmp" "$NEW_HOME/.env"
  fi

  # Archive the old dir so a re-run is a no-op and rollback is possible.
  backup="${OLD_HOME}.pre-migrate.$(date +%Y%m%d-%H%M%S)"
  mv "$OLD_HOME" "$backup"
  log "Old store archived at $backup"
fi

# --- 4. Drop Environment=HOME= from gateway/dashboard units ----------------
# Hermes CLI may have rewritten the gateway unit already; the dashboard unit
# from install.sh still carries `Environment=HOME=/opt/hermes`. A small
# drop-in scrubs HOME from both regardless of what the main unit says.
log "Installing systemd drop-ins to unset HOME override..."
for svc in hermes-gateway hermes-dashboard; do
  dir="/etc/systemd/system/${svc}.service.d"
  mkdir -p "$dir"
  cat > "$dir/10-hermes-home.conf" <<'EOF'
[Service]
# Empty `Environment=HOME=` first clears any main-unit value, then we
# leave HOME unset so systemd derives it from User= (root -> /root).
Environment=HOME=
Environment=HOME=/root
EOF
done
systemctl daemon-reload

# --- 5. Strip HERMES_HOME from /opt/hermes/.env if present -----------------
# Older install.sh wrote this. With the new layout we want the CLI default,
# so the line must go (or be set to /root/.hermes for explicitness).
if grep -q "^HERMES_HOME=" /opt/hermes/.env 2>/dev/null; then
  log "Removing HERMES_HOME override from /opt/hermes/.env..."
  sed -i '/^HERMES_HOME=/d' /opt/hermes/.env
fi

# --- 6. Profile.d export for interactive shells ----------------------------
# Default is already /root/.hermes for root via $HOME, so this is just for
# explicit visibility (and survives if you switch to a non-root admin).
cat > /etc/profile.d/hermes.sh <<'EOF'
export HERMES_HOME=/root/.hermes
EOF
chmod 644 /etc/profile.d/hermes.sh

# --- 7. Restart + verify ---------------------------------------------------
log "Restarting hermes.target..."
systemctl start hermes.target
sleep 3

log ""
log "=== Verify: all services should agree on HERMES_HOME=/root/.hermes ==="
for svc in hermes-gateway hermes-dashboard hermes-mgmt; do
  if systemctl is-active --quiet "$svc"; then
    pid=$(systemctl show -p MainPID --value "$svc")
    home=$(tr '\0' '\n' < "/proc/$pid/environ" | grep '^HOME=' || echo "HOME=(unset)")
    hh=$(tr '\0' '\n' < "/proc/$pid/environ" | grep '^HERMES_HOME=' || echo "HERMES_HOME=(unset)")
    log "  $svc (pid=$pid): $home, $hh"
  else
    log "  $svc: NOT RUNNING — check journalctl -u $svc"
  fi
done

log ""
log "Done. From now on:"
log "  - hermes config set <K> <V>           writes to /root/.hermes/.env"
log "  - Web Dashboard provider settings     reads/writes /root/.hermes/.env"
log "  - mgmt-api PUT /api/env/<K>           writes to /root/.hermes/.env (after upgrade)"
log "All three on the same store."
