#!/usr/bin/env bash
# Run inside the existing LXC: sudo bash update-service.sh <reviewed-full-commit-sha>
# Failure after the maintenance pause leaves services stopped for operator recovery.
set -euo pipefail
umask 077
APP_DIR="/srv/bandmate"
BACKUP_ROOT="/var/backups/bandmate"
MAINTENANCE=0
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
trap 'echo "Update failed. No automatic rollback was attempted." >&2; if [[ "$MAINTENANCE" == 1 ]]; then echo "Maintenance may be partial. Inspect services and the release journal before recovery; see docs/booking/OPERATIONS.md." >&2; fi' ERR

[[ "$(id -u)" == 0 ]] || { echo 'Run as root inside the verified BandMate LXC.' >&2; exit 1; }
[[ "${1:-}" =~ ^[0-9a-f]{40}$ ]] || { echo 'Supply the reviewed full 40-character commit SHA.' >&2; exit 1; }
RELEASE_SHA="$1"
[[ -d "$APP_DIR/.git" && -f "$APP_DIR/.env" && -f "$APP_DIR/db.sqlite3" ]]
cd "$APP_DIR"
exec 9>/run/lock/bandmate-update.lock
flock -n 9 || { echo 'Another deployment is running.' >&2; exit 1; }
[[ -z "$(git status --porcelain)" ]] || { echo 'Working tree is dirty; preserve and review changes first.' >&2; exit 1; }
OLD_SHA="$(git rev-parse HEAD)"
git fetch origin
git cat-file -e "${RELEASE_SHA}^{commit}"
git merge-base --is-ancestor "$OLD_SHA" "$RELEASE_SHA" || { echo 'Release must fast-forward the installed commit.' >&2; exit 1; }
# The environment file is operator-owned, trusted shell-compatible configuration.
set -a
source .env
set +a
.venv/bin/python manage.py check
.venv/bin/python manage.py migrate --check
command -v rubberband >/dev/null
rubberband --version >/dev/null
# These release tools must have been staged/reviewed before the maintenance pause.
[[ -f "$SCRIPT_DIR/ops/backup_sqlite.py" ]]
mkdir -p "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"
RELEASE_DIR="$(mktemp -d "$BACKUP_ROOT/release-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
printf 'previous=%s\nrelease=%s\n' "$OLD_SHA" "$RELEASE_SHA" > "$RELEASE_DIR/release.txt"

# Stop timers before their services so nothing starts during migration.
ACTIVE_UNITS=()
for unit in booking-scheduler.timer bandmate-google-calendar-sync.timer bandmate-calendar-sync.timer booking-worker.service booking-scheduler.service bandmate-google-calendar-sync.service bandmate-calendar-sync.service bandmate.service; do
    if systemctl is-active --quiet "$unit"; then
        ACTIVE_UNITS+=("$unit")
        printf '%s\n' "$unit" >> "$RELEASE_DIR/active-units.txt"
    fi
done
MAINTENANCE=1
for unit in booking-scheduler.timer bandmate-google-calendar-sync.timer bandmate-calendar-sync.timer booking-worker.service booking-scheduler.service bandmate-google-calendar-sync.service bandmate-calendar-sync.service bandmate.service; do
    if [[ "$(systemctl show --property=LoadState --value "$unit")" == loaded ]]; then
        systemctl stop "$unit"
    fi
done
.venv/bin/python "$SCRIPT_DIR/ops/backup_sqlite.py" db.sqlite3 "$RELEASE_DIR/db.sqlite3"
echo "Verified backup and release journal: $RELEASE_DIR"

git merge --ff-only "$RELEASE_SHA"
[[ "$(git rev-parse HEAD)" == "$RELEASE_SHA" ]]
.venv/bin/pip install -r requirements.txt --no-cache-dir
.venv/bin/python manage.py check
.venv/bin/python manage.py makemigrations --check --dry-run
.venv/bin/python manage.py migrate --noinput
.venv/bin/python manage.py migrate --check
.venv/bin/python manage.py collectstatic --noinput --verbosity 0
.venv/bin/python manage.py booking_check
# Unit installation/upgrades stay explicit to preserve operator customizations.
# Existing ICS and Google timers are independent of the per-band Booking toggle.
for unit in "${ACTIVE_UNITS[@]}"; do
    case "$unit" in
        *.timer|booking-worker.service|bandmate.service) systemctl start "$unit" ;;
        *) ;; # Do not replay one-shot work; the restored timer will schedule it.
    esac
done
for unit in "${ACTIVE_UNITS[@]}"; do
    case "$unit" in
        *.timer|booking-worker.service|bandmate.service) systemctl is-active --quiet "$unit" ;;
    esac
done
MAINTENANCE=0
printf 'completed=%s\n' "$(date -u +%Y%m%dT%H%M%SZ)" >> "$RELEASE_DIR/release.txt"
echo 'Update complete. Verify login, songs, setlists, ICS and per-band feature access.'
