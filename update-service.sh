#!/usr/bin/env bash
# ============================================================================
# BandMate — Update Script
#
# Run this INSIDE your LXC container (or any server running BandMate):
#   bash update-service.sh
# ============================================================================

set -euo pipefail

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

APP_DIR="/srv/bandmate"

if [[ ! -d "$APP_DIR/.git" ]]; then
    red "ERROR: $APP_DIR does not contain a git repo."
    exit 1
fi

cd "$APP_DIR"

bold "[1/5] Pulling latest code..."
git pull

bold "[2/5] Installing dependencies..."
source .venv/bin/activate
pip install -r requirements.txt -q

bold "[3/5] Running migrations..."
python manage.py migrate --noinput

bold "[4/5] Collecting static files..."
python manage.py collectstatic --noinput

bold "[5/5] Restarting service..."
systemctl restart bandmate

echo ""
green "BandMate updated successfully!"
echo ""
echo "  Check status:  systemctl status bandmate"
echo "  View logs:     journalctl -u bandmate -f"
echo ""
