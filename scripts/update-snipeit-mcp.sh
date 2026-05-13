#!/usr/bin/env bash
#
# Routine update of the Snipe-IT MCP server on a production VM.
#
# Run as root (or via sudo). Pulls the latest source, re-syncs the venv,
# and restarts the systemd service. Safe to re-run.
#
#   sudo /opt/snipeit-mcp/scripts/update-snipeit-mcp.sh

set -euo pipefail

SOURCE_DIR=${SOURCE_DIR:-/opt/snipeit-mcp}
STATE_DIR=${STATE_DIR:-/var/lib/snipeit-mcp}
SERVICE_USER=snipeit-mcp
UV_BIN=/usr/local/bin/uv

log() { echo "[update-snipeit-mcp] $*" >&2; }
die() { echo "[update-snipeit-mcp] ERROR: $*" >&2; exit 1; }

ENV_FILE=${ENV_FILE:-/etc/snipeit-mcp.env}

[[ $EUID -eq 0 ]] || die "Must be run as root (try: sudo $0)"
[[ -d $SOURCE_DIR/.git ]] || die "$SOURCE_DIR is not a git checkout"
[[ -x $UV_BIN ]] || die "uv not found at $UV_BIN — run setup-snipeit-mcp.sh first"
command -v curl >/dev/null || die "curl is required for the post-restart probe"
systemctl list-unit-files snipeit-mcp.service >/dev/null \
    || die "snipeit-mcp.service is not installed — run setup-snipeit-mcp.sh first"

# Run git as the source tree's owner so we don't trip git's safe.directory
# protection (which fires when running git as root against a non-root-owned
# repo). Same reason we run uv sync as the service user below.
current_branch=$(sudo --user="$SERVICE_USER" git -C "$SOURCE_DIR" rev-parse --abbrev-ref HEAD)
log "Fetching latest on branch '$current_branch' ..."
sudo --user="$SERVICE_USER" git -C "$SOURCE_DIR" fetch --quiet origin "$current_branch"
# Hard-reset rather than pull --ff-only: this VM mirrors the remote and is
# not expected to carry local commits, so we want clean recovery from
# upstream history rewrites (e.g. force-pushed feature branches) instead of
# a "diverging branches" failure that demands manual intervention.
log "Resetting to origin/$current_branch ..."
sudo --user="$SERVICE_USER" git -C "$SOURCE_DIR" reset --hard "origin/$current_branch"

log "Re-syncing venv ..."
sudo --user="$SERVICE_USER" \
    env HOME="$STATE_DIR" \
    "$UV_BIN" sync --project "$SOURCE_DIR"

log "Restarting snipeit-mcp.service ..."
systemctl restart snipeit-mcp.service
sleep 1
# `|| true` swallows the SIGPIPE that head causes on early close — would
# otherwise be fatal under `set -o pipefail` and kill the script before the
# OAuth probe runs.
systemctl status snipeit-mcp.service --no-pager | head -10 || true

# Local OAuth metadata probe — confirms the restart didn't break anything.
# Port comes from the systemd unit's Environment= directive, which we mirror
# via the same default the setup script uses.
probe_port=$(systemctl show snipeit-mcp.service --property=Environment --value \
    | tr ' ' '\n' | grep '^MCP_PORT=' | cut -d= -f2)
probe_port=${probe_port:-8000}
probe_url="http://127.0.0.1:${probe_port}/.well-known/oauth-authorization-server"
log ""
log "Probing $probe_url ..."
for attempt in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -m 3 -o /dev/null "$probe_url"; then
        log "  OAuth metadata endpoint OK"
        break
    fi
    [[ $attempt -lt 10 ]] && sleep 1
    if [[ $attempt -eq 10 ]]; then
        log "  WARNING: $probe_url did not respond within ~10s."
        log "  Check:   journalctl -u snipeit-mcp.service -n 30 --no-pager"
    fi
done

log ""
log "Logs:  journalctl -u snipeit-mcp.service -f"
