#!/usr/bin/env bash
# ============================================================================
# BandMate — Service Deployment Script
#
# Run this INSIDE your LXC container (or any Debian/Ubuntu server):
#   bash deploy-service-only.sh
#
# It will install all dependencies, clone your repo, and start BandMate
# as a systemd service, with optional Cloudflare Tunnel for HTTPS.
# ============================================================================

set -euo pipefail

# ---------- colour helpers --------------------------------------------------
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

# ---------- pre-flight check -----------------------------------------------
if [[ "$(id -u)" -ne 0 ]]; then
    red "ERROR: This script must be run as root."
    exit 1
fi

# ---------- gather configuration --------------------------------------------
bold "=== BandMate Service Deployer ==="
echo ""

read -rp "GitHub repo URL [https://github.com/you/bandmate.git]: " REPO_URL
if [[ -z "$REPO_URL" ]]; then
    red "A GitHub repo URL is required."
    exit 1
fi

read -rp "Admin username [admin]: " ADMIN_USER
ADMIN_USER="${ADMIN_USER:-admin}"

read -rsp "Admin password: " ADMIN_PASS
echo ""
if [[ -z "$ADMIN_PASS" ]]; then
    red "Admin password cannot be empty."
    exit 1
fi

read -rp "Port for Gunicorn [80]: " PORT
PORT="${PORT:-80}"

ENABLE_TUNNEL="n"
CF_HOSTNAME=""
CF_TOKEN=""
echo ""
read -rp "Set up Cloudflare Tunnel for HTTPS? [y/N]: " ENABLE_TUNNEL
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    echo ""
    bold "Cloudflare Tunnel Setup"
    echo "  Before continuing, create a tunnel in the Cloudflare dashboard:"
    echo ""
    echo "  1. Go to https://one.dash.cloudflare.com"
    echo "  2. Select your account > Networks > Tunnels > Create a tunnel"
    echo "  3. Choose 'Cloudflared' as the connector type"
    echo "  4. Name it (e.g. 'bandmate')"
    echo "  5. Select 'Debian' and '64-bit'"
    echo "  6. Copy the tunnel token from the install command"
    echo "     (the long string after --token)"
    echo "  7. Do NOT click Continue yet -- leave the dashboard open"
    echo ""
    echo "  After the deploy script finishes, go back to the dashboard to"
    echo "  configure the public hostname (see PROXMOX.md for details)."
    echo ""
    read -rp "Public hostname (e.g. bandmate.thedeadend.band): " CF_HOSTNAME
    if [[ -z "$CF_HOSTNAME" ]]; then
        red "Hostname cannot be empty when Cloudflare Tunnel is enabled."
        exit 1
    fi
    read -rp "Cloudflare Tunnel token: " CF_TOKEN
    if [[ -z "$CF_TOKEN" ]]; then
        red "Tunnel token cannot be empty."
        exit 1
    fi
fi

if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    TOTAL_STEPS=6
else
    TOTAL_STEPS=5
fi

echo ""
bold "Configuration:"
echo "  Repo         : $REPO_URL"
echo "  Admin user   : $ADMIN_USER"
echo "  Port         : $PORT"
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
echo "  Tunnel       : Cloudflare"
echo "  Hostname     : $CF_HOSTNAME"
fi
echo ""
read -rp "Proceed? [Y/n] " CONFIRM
if [[ "${CONFIRM,,}" == "n" ]]; then
    echo "Aborted."; exit 0
fi

# ---------- install system packages -----------------------------------------
bold "[1/${TOTAL_STEPS}] Installing system packages (python3, ffmpeg, git, openssh)..."
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git openssh-server > /dev/null 2>&1
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
systemctl enable ssh > /dev/null 2>&1
systemctl restart ssh

# ---------- clone repo and install app --------------------------------------
bold "[2/${TOTAL_STEPS}] Cloning repo and installing Python dependencies..."
mkdir -p /srv/bandmate
cd /srv/bandmate
if [[ -d ".git" ]]; then
    echo "Repo already cloned. Pulling latest..."
    git pull
else
    git clone "$REPO_URL" .
fi
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

# ---------- configure environment -------------------------------------------
bold "[3/${TOTAL_STEPS}] Configuring environment..."
SECRET_KEY=$(.venv/bin/python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())")

MY_IP=$(hostname -I | awk '{print $1}' 2>/dev/null || echo "127.0.0.1")

ENV_CONTENT="DJANGO_SECRET_KEY=${SECRET_KEY}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=127.0.0.1,${MY_IP}${CF_HOSTNAME:+,${CF_HOSTNAME}}
SONGS_DIR=/srv/bandmate/songs
WAVEFORM_CACHE_DIR=/srv/bandmate/.waveform_cache"

if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    ENV_CONTENT="${ENV_CONTENT}
SECURE_PROXY_SSL_HEADER=1
CSRF_TRUSTED_ORIGINS=https://${CF_HOSTNAME}"
fi

cat > /srv/bandmate/.env << ENVEOF
${ENV_CONTENT}
ENVEOF

# ---------- initialise database and static files ----------------------------
bold "[4/${TOTAL_STEPS}] Running migrations, collecting static files, creating admin..."
cd /srv/bandmate
set -a && source .env && set +a
mkdir -p songs .waveform_cache
.venv/bin/python manage.py migrate --noinput -q
.venv/bin/python manage.py collectstatic --noinput -q
.venv/bin/python manage.py shell -c "
from django.contrib.auth.models import User
if not User.objects.filter(username='${ADMIN_USER}').exists():
    User.objects.create_superuser('${ADMIN_USER}', '', '${ADMIN_PASS}')
    print('Admin user created.')
else:
    print('Admin user already exists.')
"

# ---------- create systemd service ------------------------------------------
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    BIND_ADDR="127.0.0.1:${PORT}"
else
    BIND_ADDR="0.0.0.0:${PORT}"
fi

bold "[5/${TOTAL_STEPS}] Installing systemd service..."
cat > /etc/systemd/system/bandmate.service << SVCEOF
[Unit]
Description=BandMate Audio Player
After=network.target

[Service]
WorkingDirectory=/srv/bandmate
EnvironmentFile=/srv/bandmate/.env
ExecStart=/srv/bandmate/.venv/bin/gunicorn bandmate.wsgi:application \
    --bind ${BIND_ADDR} \
    --workers 3 \
    --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable bandmate
systemctl start bandmate

# ---------- install Cloudflare Tunnel if enabled ----------------------------
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    bold "[6/${TOTAL_STEPS}] Installing cloudflared and configuring tunnel..."
    apt-get install -y -qq curl > /dev/null 2>&1
    curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb
    dpkg -i /tmp/cloudflared.deb > /dev/null 2>&1
    rm -f /tmp/cloudflared.deb

    cloudflared service install "${CF_TOKEN}"

    echo ""
    echo "  Cloudflare Tunnel connector is running."
fi

# ---------- done -----------------------------------------------------------
echo ""
green "============================================"
green "  BandMate deployed successfully!"
green "============================================"
echo ""
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
echo "  URL          : https://${CF_HOSTNAME}"
else
echo "  URL          : http://${MY_IP}:${PORT}"
fi
echo "  Admin user   : $ADMIN_USER"
echo ""
echo "  Manage the service:"
echo "    systemctl status bandmate"
echo "    journalctl -u bandmate -f"
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
echo "    systemctl status cloudflared"
echo "    journalctl -u cloudflared -f"
echo ""
echo "  NEXT STEP: Go back to the Cloudflare dashboard and click Continue."
echo "  Add a Public Hostname:"
echo "    Subdomain: (e.g. bandmate)  Domain: (e.g. thedeadend.band)"
echo "    Service: HTTP  URL: localhost:${PORT}"
echo "  Save the tunnel. Your site will be live at https://${CF_HOSTNAME}"
fi
echo ""
echo "  Upload songs via the web UI or SCP them to the container:"
echo "    scp -r /path/to/songs/* root@${MY_IP}:/srv/bandmate/songs/"
echo ""
