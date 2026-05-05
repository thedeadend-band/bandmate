#!/usr/bin/env bash
# ============================================================================
# BandMate — Update Script
#
# Run this INSIDE your LXC container (or any server running BandMate):
#   bash update-service.sh
# ============================================================================

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }
yellow() { printf '\033[1;33m%s\033[0m\n' "$*"; }

APP_DIR="/srv/bandmate"
FAILED=0

if [[ ! -d "$APP_DIR/.git" ]]; then
    red "ERROR: $APP_DIR does not contain a git repo."
    exit 1
fi

cd "$APP_DIR"

# ---------- Step 1: Pull code ------------------------------------------------
bold "[1/5] Pulling latest code..."
if git pull; then
    green "  ✓ Code updated"
else
    red "  ✗ git pull failed (exit code $?)"
    red "    Check network connectivity or resolve merge conflicts."
    FAILED=1
fi

# ---------- Step 2: Install dependencies -------------------------------------
bold "[2/5] Installing dependencies..."
source .venv/bin/activate
if pip install -r requirements.txt --no-cache-dir -q 2>&1 | tee /tmp/bandmate-pip.log; then
    green "  ✓ Dependencies installed"
else
    red "  ✗ pip install failed (exit code $?)"
    red "    See full log: /tmp/bandmate-pip.log"
    yellow "    Common fixes:"
    yellow "      - Check memory: free -h"
    yellow "      - Retry: pip install -r requirements.txt --no-cache-dir --retries 5 --timeout 120"
    FAILED=1
fi

# ---------- Step 3: Run migrations -------------------------------------------
bold "[3/5] Running migrations..."
echo "  Pending migrations:"
python manage.py showmigrations --list 2>/dev/null | grep "\[ \]" || echo "    (none)"
echo ""

if python manage.py migrate --noinput 2>&1 | tee /tmp/bandmate-migrate.log; then
    green "  ✓ Migrations applied"
else
    red "  ✗ Migrations failed (exit code $?)"
    red "    See full log: /tmp/bandmate-migrate.log"
    yellow "    Debug with: python manage.py migrate --noinput -v 2"
    yellow "    Check status: python manage.py showmigrations"
    FAILED=1
fi

# Verify no unapplied migrations remain
if ! python manage.py migrate --check > /dev/null 2>&1; then
    red "  ✗ WARNING: Unapplied migrations still exist after migrate!"
    python manage.py showmigrations --list 2>/dev/null | grep "\[ \]"
    FAILED=1
fi

# ---------- Step 4: Collect static files -------------------------------------
bold "[4/5] Collecting static files..."
if python manage.py collectstatic --noinput --verbosity 0; then
    green "  ✓ Static files collected"
else
    red "  ✗ collectstatic failed"
    FAILED=1
fi

# ---------- Step 5: Restart service ------------------------------------------
bold "[5/5] Restarting service..."
systemctl restart bandmate

sleep 2
if systemctl is-active --quiet bandmate; then
    green "  ✓ Service running"
else
    red "  ✗ Service failed to start!"
    red "    Recent logs:"
    journalctl -u bandmate --no-pager -n 15
    FAILED=1
fi

# ---------- Summary ----------------------------------------------------------
echo ""
if [[ $FAILED -eq 0 ]]; then
    green "============================================"
    green "  BandMate updated successfully!"
    green "============================================"
else
    red "============================================"
    red "  Update completed with errors (see above)"
    red "============================================"
fi
echo ""
echo "  Check status:  systemctl status bandmate"
echo "  View logs:     journalctl -u bandmate -f"
echo ""
