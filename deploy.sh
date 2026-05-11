#!/usr/bin/env bash
# =============================================================================
# mario_robotta / wg-cop — server reference for agents and humans
# =============================================================================
#
# PRIMARY DEPLOY METHOD: GitHub Actions (100% automated)
#   Trigger : push to `main` branch
#   Workflow : .github/workflows/deploy.yml
#   What it does:
#     ssh into server → git reset --hard origin/main → pip install → systemctl restart
#   To deploy: merge your branch to main (PR or direct push to main).
#   Do NOT scp code, do NOT git push to the server manually.
#
# EMERGENCY FALLBACK (GitHub Actions broken):
#   Use the parent deploy.sh, two directories above this file:
#     ../../deploy.sh mario_robotta
#   That script rsyncs from your local main to the server.
#   Git remote alias for this repo on your Mac: jaenib-wgcop
#
# =============================================================================
# SERVER
# =============================================================================
#   Host        : 82.165.45.100
#   User        : root
#   SSH         : ssh root@82.165.45.100
#   Bot dir     : /usr/bots/wg_cop/
#   Service     : wg_cop   (systemctl)
#   Venv        : /usr/bots/wg_cop/venv/
#
# Files NOT in git (stay on server):
#   config.py            — bot token + Telegram IDs (secrets)
#   wg_data_alpha.json   — live expense/chore/penalty database
#
# =============================================================================
# UTILITY COMMANDS
# =============================================================================

set -euo pipefail

SERVER="root@82.165.45.100"
BOT_DIR="/usr/bots/wg_cop"
SERVICE="wg_cop"

case "${1:-help}" in

  status)
    ssh "$SERVER" "systemctl status $SERVICE --no-pager && journalctl -u $SERVICE -n 40 --no-pager"
    ;;

  logs)
    ssh "$SERVER" "journalctl -u $SERVICE -f"
    ;;

  restart)
    echo "Restarting $SERVICE …"
    ssh "$SERVER" "systemctl restart $SERVICE && systemctl status $SERVICE --no-pager"
    ;;

  data)
    # Download live database
    scp "$SERVER:$BOT_DIR/wg_data_alpha.json" /tmp/wg_data_live.json
    echo "Saved to /tmp/wg_data_live.json"
    ;;

  push-data)
    # Emergency: upload a repaired database and restart
    # Usage: ./deploy.sh push-data /path/to/wg_data_alpha.json
    LOCAL="${2:?usage: deploy.sh push-data <file.json>}"
    echo "Uploading $LOCAL → $SERVER:$BOT_DIR/wg_data_alpha.json (restarting in 5s…)"
    sleep 5
    scp "$LOCAL" "$SERVER:$BOT_DIR/wg_data_alpha.json"
    ssh "$SERVER" "systemctl restart $SERVICE"
    echo "Done."
    ;;

  help|*)
    grep "^#" "$0" | sed 's/^# \{0,1\}//'
    echo ""
    echo "Usage: $0 {status|logs|restart|data|push-data <file>}"
    ;;

esac
