#!/usr/bin/env bash
# =============================================================================
# wg-cop — server reference for agents and humans
# =============================================================================
#
# DEPLOY METHOD: 100% GitHub Actions — do NOT manually push code to the server.
#   Trigger: push to `main` branch → .github/workflows/deploy.yml fires.
#   The workflow SSHes into the server and runs: git reset --hard origin/main,
#   pip install, systemctl restart wg_cop.
#
# To deploy: merge your branch to main (via PR or direct push).
#
# =============================================================================
# SERVER ACCESS
# =============================================================================
#   Host : 82.165.45.100
#   User : root
#   SSH  : ssh root@82.165.45.100
#   (Key must be configured in your local ~/.ssh or provided as SSH_KEY secret)
#
# =============================================================================
# SERVER LAYOUT
# =============================================================================
#   /usr/bots/wg_cop/          — git repo (tracks origin/main)
#   /usr/bots/wg_cop/venv/     — Python virtualenv
#   /usr/bots/wg_cop/config.py — secrets (NOT in git, stays on server)
#   /usr/bots/wg_cop/wg_data_alpha.json  — live database (NOT in git)
#
# =============================================================================
# USEFUL COMMANDS (run on the server via ssh)
# =============================================================================

set -euo pipefail

SERVER="root@82.165.45.100"
BOT_DIR="/usr/bots/wg_cop"
SERVICE="wg_cop"

cmd() { ssh "$SERVER" "$@"; }

case "${1:-help}" in

  status)
    # Show systemd service status and last 40 log lines
    cmd "systemctl status $SERVICE --no-pager && journalctl -u $SERVICE -n 40 --no-pager"
    ;;

  logs)
    # Stream live logs  (Ctrl-C to stop)
    ssh "$SERVER" "journalctl -u $SERVICE -f"
    ;;

  restart)
    # Emergency restart without a full deploy (no code change)
    echo "Restarting $SERVICE on $SERVER …"
    cmd "systemctl restart $SERVICE"
    cmd "systemctl status $SERVICE --no-pager"
    ;;

  data)
    # Download the live database to /tmp/wg_data_live.json
    scp "$SERVER:$BOT_DIR/wg_data_alpha.json" /tmp/wg_data_live.json
    echo "Saved to /tmp/wg_data_live.json"
    ;;

  push-data)
    # Upload a local JSON file to the server (emergency data restore)
    # Usage: ./deploy.sh push-data /path/to/file.json
    LOCAL="${2:?usage: deploy.sh push-data <local-file.json>}"
    echo "Uploading $LOCAL → $SERVER:$BOT_DIR/wg_data_alpha.json"
    echo "Press Ctrl-C within 5s to abort …"; sleep 5
    scp "$LOCAL" "$SERVER:$BOT_DIR/wg_data_alpha.json"
    cmd "systemctl restart $SERVICE"
    echo "Done. Service restarted."
    ;;

  help|*)
    grep "^#" "$0" | sed 's/^# \{0,1\}//'
    echo ""
    echo "Usage: $0 {status|logs|restart|data|push-data <file>}"
    ;;

esac
