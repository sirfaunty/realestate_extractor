#!/bin/sh
# Nightly backup for a Capactive org instance (docs/DEPLOY.md §Backups).
#
# Runs INSIDE the app container (sqlite3 is in the image):
#   docker compose exec app sh deploy/backup.sh
# or from host cron:
#   0 3 * * * cd /opt/capactive && docker compose exec -T app sh deploy/backup.sh
#
# Uses sqlite3 .backup (safe against live writers — no file-copy
# corruption), then tars synced PDFs + generated deliverables.
# Keeps RETAIN_DAYS days; copy the backup dir off-host for real DR.

set -eu

DATA="${CAPACTIVE_DATA_DIR:-/app/realestate_extractor/data}"
DEST="$DATA/backups/$(date +%Y-%m-%d_%H%M)"
RETAIN_DAYS="${CAPACTIVE_BACKUP_RETAIN_DAYS:-14}"

mkdir -p "$DEST"

# every sqlite database under data/ (org DBs + config DB)
for db in "$DATA"/*.db; do
    [ -f "$db" ] || continue
    sqlite3 "$db" ".backup '$DEST/$(basename "$db")'"
    echo "backed up $(basename "$db")"
done

# source PDFs + deliverables (content-addressed / regenerable, but cheap
# to keep and they complete a bare-metal restore)
for d in synced_pdfs deliverables; do
    if [ -d "$DATA/$d" ]; then
        tar -czf "$DEST/$d.tar.gz" -C "$DATA" "$d"
        echo "archived $d"
    fi
done

# retention
find "$DATA/backups" -mindepth 1 -maxdepth 1 -type d \
    -mtime +"$RETAIN_DAYS" -exec rm -rf {} +

echo "backup complete: $DEST"
