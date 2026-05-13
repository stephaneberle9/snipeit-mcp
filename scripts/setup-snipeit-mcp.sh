#!/usr/bin/env bash
#
# Production VM installer for the Snipe-IT MCP server (OAuth / HTTP mode).
#
# Run as root (or via sudo) on a fresh VM. Idempotent — re-running is safe.
#
#   git clone --branch feature/interactive-oauth-login \
#       https://github.com/stephaneberle9/snipeit-mcp.git /opt/snipeit-mcp
#   # Optional: drop a .env at the repo root with the deployment-specific values
#   # (SNIPEIT_URL, SNIPEIT_MCP_BASE_URL, SNIPEIT_OAUTH_CLIENT_ID/_SECRET).
#   # Setup seeds /etc/snipeit-mcp.env from it; missing keys become __FILL_ME__.
#   sudo bash /opt/snipeit-mcp/scripts/setup-snipeit-mcp.sh
#
# Configures:
#   /opt/snipeit-mcp/                        — source tree (service-user-owned)
#   /var/lib/snipeit-mcp/                    — FASTMCP_HOME (DCR storage)
#   /etc/snipeit-mcp.env                     — secrets + config (root:snipeit-mcp, 0640)
#   /etc/systemd/system/snipeit-mcp.service  — systemd unit
#
# The OAuth client itself is registered separately in the Snipe-IT admin UI
# (Settings → OAuth Clients) with redirect URI <SNIPEIT_MCP_BASE_URL>/auth/callback.

set -euo pipefail

SOURCE_DIR=${SOURCE_DIR:-/opt/snipeit-mcp}
STATE_DIR=${STATE_DIR:-/var/lib/snipeit-mcp}
ENV_FILE=${ENV_FILE:-/etc/snipeit-mcp.env}
# Optional seed file — if present, SNIPEIT_OAUTH_CLIENT_ID / _SECRET are
# extracted from it and pre-filled into ENV_FILE during first-time generation.
# Defaults to a `.env` at the repo root, which is convenient if you `scp` one
# over from your dev machine.
SEED_ENV_FILE=${SEED_ENV_FILE:-$SOURCE_DIR/.env}
UNIT_FILE=/etc/systemd/system/snipeit-mcp.service
SERVICE_USER=snipeit-mcp
UV_BIN=/usr/local/bin/uv

# Infra-level settings — script-controlled, baked into the systemd unit's
# Environment= directives. Override at install time with e.g.
# `sudo MCP_PORT=9000 bash setup-snipeit-mcp.sh`.
MCP_TRANSPORT=${MCP_TRANSPORT:-http}
MCP_HOST=${MCP_HOST:-0.0.0.0}
MCP_PORT=${MCP_PORT:-8000}
LOG_LEVEL=${LOG_LEVEL:-INFO}
FASTMCP_HOME=${FASTMCP_HOME:-$STATE_DIR}

log() { echo "[setup-snipeit-mcp] $*" >&2; }
die() { echo "[setup-snipeit-mcp] ERROR: $*" >&2; exit 1; }

# ---- 0. Pre-flight ----------------------------------------------------------
[[ $EUID -eq 0 ]] || die "Must be run as root (try: sudo $0)"
[[ -d $SOURCE_DIR/.git ]] || die "$SOURCE_DIR is not a git checkout. Clone the repo there first."

for cmd in git curl systemctl chown chmod useradd id getent hostname; do
    command -v "$cmd" >/dev/null || die "Missing required command: $cmd"
done

# ---- 0a. Make the local hostname resolvable --------------------------------
# On freshly-provisioned VMs the hostname often isn't in /etc/hosts, which
# makes every sudo invocation print 'unable to resolve host' warnings.
# Add a 127.0.1.1 entry if missing.
hostname_short=$(hostname -s)
if ! getent hosts "$hostname_short" >/dev/null; then
    log "Adding '$hostname_short' to /etc/hosts so sudo can resolve it"
    echo "127.0.1.1 $hostname_short" >>/etc/hosts
fi

# ---- 1. Install uv if missing -----------------------------------------------
if [[ ! -x $UV_BIN ]]; then
    log "Installing uv to $(dirname "$UV_BIN") ..."
    curl -LsSf https://astral.sh/uv/install.sh \
        | env UV_INSTALL_DIR="$(dirname "$UV_BIN")" sh
    [[ -x $UV_BIN ]] || die "uv install failed"
else
    log "uv already installed at $UV_BIN ($("$UV_BIN" --version))"
fi

# ---- 2. Service user --------------------------------------------------------
if id "$SERVICE_USER" >/dev/null 2>&1; then
    log "Service user '$SERVICE_USER' already exists"
else
    log "Creating service user '$SERVICE_USER' ..."
    useradd --system \
        --home-dir "$STATE_DIR" --create-home \
        --shell /usr/sbin/nologin "$SERVICE_USER"
fi

# ---- 3. State directory (FASTMCP_HOME) --------------------------------------
mkdir -p "$STATE_DIR"
chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE_DIR"
chmod 0750 "$STATE_DIR"

# ---- 4. Sync the venv (creates $SOURCE_DIR/.venv) ---------------------------
# The service user owns the whole source tree so it can create and update its
# own .venv. We rely on systemd hardening (NoNewPrivileges, ProtectSystem, …)
# for isolation, not on the source being root-owned.
log "Running 'uv sync' in $SOURCE_DIR ..."
chown -R "$SERVICE_USER:$SERVICE_USER" "$SOURCE_DIR"
sudo --user="$SERVICE_USER" \
    env HOME="$STATE_DIR" \
    "$UV_BIN" sync --project "$SOURCE_DIR"

# ---- 5. Env file (template only if missing — never clobber a real one) ------
if [[ ! -f $ENV_FILE ]]; then
    # Extract deployment-specific values from the seed .env if present.
    # We pull URLs and OAuth credentials — anything that differs between
    # deployments. Infra-level settings (MCP_HOST, FASTMCP_HOME, …) stay
    # hard-coded in the template below.
    seed() {
        local key=$1
        [[ -f $SEED_ENV_FILE ]] || return 0
        local value
        value=$(grep -E "^${key}=" "$SEED_ENV_FILE" \
            | head -1 | cut -d= -f2- | sed -e 's/^["'"'"']//' -e 's/["'"'"']$//')
        [[ -n $value ]] && log "  found $key" >&2
        printf '%s' "$value"
    }

    [[ -f $SEED_ENV_FILE ]] && log "Reading values from $SEED_ENV_FILE ..."
    seed_url=$(seed SNIPEIT_URL)
    seed_base_url=$(seed SNIPEIT_MCP_BASE_URL)
    seed_client_id=$(seed SNIPEIT_OAUTH_CLIENT_ID)
    seed_client_secret=$(seed SNIPEIT_OAUTH_CLIENT_SECRET)

    log "Writing env file $ENV_FILE"
    cat >"$ENV_FILE" <<EOF
# Snipe-IT MCP production environment — secrets and deployment-specific URLs.
# Infrastructure settings (transport, host, port, log level, FASTMCP_HOME)
# are baked into the systemd unit's Environment= directives, not here.
# Edit any __FILL_ME__ placeholders, then: sudo systemctl restart snipeit-mcp.service

SNIPEIT_URL=${seed_url:-__FILL_ME__}
SNIPEIT_OAUTH_CLIENT_ID=${seed_client_id:-__FILL_ME__}
SNIPEIT_OAUTH_CLIENT_SECRET=${seed_client_secret:-__FILL_ME__}
SNIPEIT_MCP_BASE_URL=${seed_base_url:-__FILL_ME__}
EOF
else
    log "Env file $ENV_FILE already exists — leaving in place"
fi
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 0640 "$ENV_FILE"

# ---- 6. systemd unit --------------------------------------------------------
log "Installing systemd unit at $UNIT_FILE ..."
cat >"$UNIT_FILE" <<EOF
[Unit]
Description=Snipe-IT MCP Server (OAuth, HTTP transport)
Documentation=https://github.com/stephaneberle9/snipeit-mcp
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
Group=$SERVICE_USER
WorkingDirectory=$SOURCE_DIR
EnvironmentFile=$ENV_FILE
Environment=MCP_TRANSPORT=$MCP_TRANSPORT
Environment=MCP_HOST=$MCP_HOST
Environment=MCP_PORT=$MCP_PORT
Environment=LOG_LEVEL=$LOG_LEVEL
Environment=FASTMCP_HOME=$FASTMCP_HOME
ExecStart=$UV_BIN run --project $SOURCE_DIR snipeit-mcp
Restart=on-failure
RestartSec=5

# Hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=$STATE_DIR
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
LockPersonality=true
MemoryDenyWriteExecute=true
RestrictRealtime=true
RestrictNamespaces=true

[Install]
WantedBy=multi-user.target
EOF
chmod 0644 "$UNIT_FILE"

systemctl daemon-reload

# ---- 7. Start the service (only if env is filled in) ------------------------
if grep -q '__FILL_ME__' "$ENV_FILE"; then
    log ""
    log "  ${ENV_FILE} still has __FILL_ME__ placeholders."
    log "  Edit it, then:    sudo systemctl enable --now snipeit-mcp.service"
    log ""
    exit 0
fi

log "Enabling and (re)starting snipeit-mcp.service ..."
# `enable` + `restart` (not `enable --now`): `--now` only does `start`, which is
# a no-op when the service is already active and so does *not* re-read
# EnvironmentFile= or pick up Environment= changes from the unit file we just
# rewrote. `restart` is idempotent — starts if stopped, restarts if running.
systemctl enable snipeit-mcp.service
systemctl restart snipeit-mcp.service
sleep 1
# `|| true` swallows the SIGPIPE that head causes on early close — would
# otherwise be fatal under `set -o pipefail` and kill the script before the
# OAuth probe runs.
systemctl status snipeit-mcp.service --no-pager | head -15 || true

# Local OAuth metadata probe — confirms the server is listening, transport
# is HTTP, and the OAuthProxy is wired in. Public-URL routing is a separate
# concern (DNS, reverse proxy, firewall) and not tested here.
probe_url="http://127.0.0.1:${MCP_PORT}/.well-known/oauth-authorization-server"
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
# Derive the public verify URL from what we just wrote to ENV_FILE — keeps the
# script generic across deployments.
base_url=$(grep -E '^SNIPEIT_MCP_BASE_URL=' "$ENV_FILE" | head -1 | cut -d= -f2- | tr -d ' "')
if [[ -n $base_url && $base_url != __FILL_ME__ ]]; then
    log "Verify public URL once DNS/proxy are wired:"
    log "  curl -i ${base_url%/}/.well-known/oauth-authorization-server"
fi
log "Logs:  journalctl -u snipeit-mcp.service -f"
