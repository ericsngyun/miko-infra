#!/usr/bin/env bash
set -euo pipefail

# ── Environment ──────────────────────────────────────────────────────────────
export AWS_ACCESS_KEY_ID="${B2_ACCOUNT_ID}"
export AWS_SECRET_ACCESS_KEY="${B2_ACCOUNT_KEY}"
export RESTIC_REPOSITORY="s3:s3.us-west-004.backblazeb2.com/${B2_BUCKET_NAME}"
export RESTIC_PASSWORD="${RESTIC_PASSWORD}"

BACKUP_DIRS="/data/postgres /data/qdrant"
RETENTION_DAILY=7
RETENTION_WEEKLY=4
RETENTION_MONTHLY=3

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

init_repo() {
    if ! restic snapshots &>/dev/null; then
        log "Initializing Restic repository..."
        restic init
        log "Repository initialized."
    fi
}

run_backup() {
    log "Starting backup..."
    restic backup ${BACKUP_DIRS} \
        --tag "node1" \
        --tag "$(date -u '+%Y-%m-%d')"
    log "Backup complete."

    log "Pruning old snapshots..."
    restic forget \
        --keep-daily  ${RETENTION_DAILY} \
        --keep-weekly ${RETENTION_WEEKLY} \
        --keep-monthly ${RETENTION_MONTHLY} \
        --prune
    log "Prune complete."
}

notify() {
    local status="$1"
    local msg="$2"
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -n "${TELEGRAM_CHAT_ID:-}" ]]; then
        curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
            -d chat_id="${TELEGRAM_CHAT_ID}" \
            -d text="${msg}" \
            -d parse_mode="Markdown" > /dev/null
    fi
}

daemon_mode() {
    log "Backup agent started — daemon mode. Will run at 02:30 UTC daily."
    init_repo

    while true; do
        # Calculate seconds until next 02:30 UTC (Alpine-compatible)
        now=$(date -u +%s)
        today=$(date -u '+%Y-%m-%d')
        target_str="${today} 02:30:00"
        target=$(date -u -d "${target_str}" +%s 2>/dev/null ||                  awk "BEGIN{print $(date -u +%s) - $(date -u +%H)*3600 - $(date -u +%M)*60 - $(date -u +%S) + 2*3600 + 30*60}")

        # If target is in the past, add 24 hours
        if [[ ${target} -le ${now} ]]; then
            target=$((target + 86400))
        fi

        sleep_secs=$((target - now))
        log "Next backup in ${sleep_secs}s at 02:30 UTC."
        sleep ${sleep_secs}

        if run_backup; then
            notify "ok" "✅ *Node 1 backup complete*\n$(date -u '+%Y-%m-%d %H:%M UTC')"
        else
            notify "fail" "🚨 *Node 1 backup FAILED*\n$(date -u '+%Y-%m-%d %H:%M UTC')"
        fi
    done
}

case "${1:-}" in
    --daemon) daemon_mode ;;
    --now)    init_repo && run_backup ;;
    *)        echo "Usage: backup.sh [--daemon|--now]"; exit 1 ;;
esac
