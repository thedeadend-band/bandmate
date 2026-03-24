#!/usr/bin/env bash
# ============================================================================
# BandMate — Proxmox LXC Deployment Script
#
# Run this on your Proxmox host:
#   bash deploy.sh
#
# It will create a Debian 12 LXC container, install all dependencies,
# clone your repo, and start BandMate as a systemd service.
# ============================================================================

set -euo pipefail

# ---------- colour helpers --------------------------------------------------
bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
green() { printf '\033[1;32m%s\033[0m\n' "$*"; }
red()   { printf '\033[1;31m%s\033[0m\n' "$*"; }

# ---------- pre-flight check -----------------------------------------------
if ! command -v pct &>/dev/null; then
    red "ERROR: 'pct' not found. This script must be run on a Proxmox host."
    exit 1
fi

# ---------- gather configuration --------------------------------------------
bold "=== BandMate Proxmox Deployer ==="
echo ""

read -rp "GitHub repo URL [https://github.com/you/bandmate.git]: " REPO_URL
if [[ -z "$REPO_URL" ]]; then
    red "A GitHub repo URL is required."
    exit 1
fi

read -rp "Container ID [200]: " CTID
CTID="${CTID:-200}"

read -rp "Storage for container rootfs [local-lvm]: " STORAGE
STORAGE="${STORAGE:-local-lvm}"

read -rp "Disk size in GB [16]: " DISK_GB
DISK_GB="${DISK_GB:-16}"

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

TEMPLATE="debian-12-standard_12.7-1_amd64.tar.zst"
TEMPLATE_STORAGE="local"

echo ""
bold "Configuration:"
echo "  Container ID : $CTID"
echo "  Storage      : $STORAGE"
echo "  Disk         : ${DISK_GB}GB"
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

if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    TOTAL_STEPS=9
else
    TOTAL_STEPS=8
fi

# ---------- download template if needed -------------------------------------
bold "[1/${TOTAL_STEPS}] Checking container template..."
if ! pveam list "$TEMPLATE_STORAGE" 2>/dev/null | grep -q "$TEMPLATE"; then
    echo "Downloading Debian 12 template..."
    pveam download "$TEMPLATE_STORAGE" "$TEMPLATE"
else
    echo "Template already available."
fi

# ---------- create container ------------------------------------------------
bold "[2/${TOTAL_STEPS}] Creating LXC container $CTID..."
pct create "$CTID" \
    "${TEMPLATE_STORAGE}:vztmpl/${TEMPLATE}" \
    --hostname bandmate \
    --rootfs "${STORAGE}:${DISK_GB}" \
    --cores 2 \
    --memory 2048 \
    --swap 512 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 \
    --features nesting=1 \
    --start 0

# ---------- start container -------------------------------------------------
bold "[3/${TOTAL_STEPS}] Starting container..."
pct start "$CTID"
sleep 5

# ---------- install system packages -----------------------------------------
bold "[4/${TOTAL_STEPS}] Installing system packages (python3, ffmpeg, git)..."
pct exec "$CTID" -- bash -c "
    apt-get update -qq &&
    apt-get install -y -qq python3 python3-venv python3-pip ffmpeg git > /dev/null 2>&1
"

# ---------- clone repo and install app --------------------------------------
bold "[5/${TOTAL_STEPS}] Cloning repo and installing Python dependencies..."
pct exec "$CTID" -- bash -c "
    mkdir -p /srv/bandmate &&
    cd /srv/bandmate &&
    git clone '$REPO_URL' . &&
    python3 -m venv .venv &&
    .venv/bin/pip install --upgrade pip -q &&
    .venv/bin/pip install -r requirements.txt -q
"

# ---------- configure environment -------------------------------------------
bold "[6/${TOTAL_STEPS}] Configuring environment..."
SECRET_KEY=$(pct exec "$CTID" -- bash -c "
    /srv/bandmate/.venv/bin/python -c \"from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())\"
")

CT_IP=$(pct exec "$CTID" -- bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null || echo "127.0.0.1")

ENV_CONTENT="DJANGO_SECRET_KEY=${SECRET_KEY}
DJANGO_DEBUG=False
DJANGO_ALLOWED_HOSTS=127.0.0.1,${CT_IP}${CF_HOSTNAME:+,${CF_HOSTNAME}}
SONGS_DIR=/srv/bandmate/songs
WAVEFORM_CACHE_DIR=/srv/bandmate/.waveform_cache"

if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    ENV_CONTENT="${ENV_CONTENT}
SECURE_PROXY_SSL_HEADER=1
CSRF_TRUSTED_ORIGINS=https://${CF_HOSTNAME}"
fi

pct exec "$CTID" -- bash -c "cat > /srv/bandmate/.env << 'ENVEOF'
${ENV_CONTENT}
ENVEOF
"

# ---------- initialise database and static files ----------------------------
bold "[7/${TOTAL_STEPS}] Running migrations, collecting static files, creating admin..."
pct exec "$CTID" -- bash -c "
    cd /srv/bandmate &&
    set -a && source .env && set +a &&
    mkdir -p songs .waveform_cache &&
    .venv/bin/python manage.py migrate --noinput -q &&
    .venv/bin/python manage.py collectstatic --noinput -q &&
    .venv/bin/python manage.py shell -c \"
from django.contrib.auth.models import User
if not User.objects.filter(username='${ADMIN_USER}').exists():
    User.objects.create_superuser('${ADMIN_USER}', '', '${ADMIN_PASS}')
    print('Admin user created.')
else:
    print('Admin user already exists.')
\"
"

# ---------- create systemd service ------------------------------------------
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    BIND_ADDR="127.0.0.1:${PORT}"
else
    BIND_ADDR="0.0.0.0:${PORT}"
fi

bold "[8/${TOTAL_STEPS}] Installing systemd service..."
pct exec "$CTID" -- bash -c "cat > /etc/systemd/system/bandmate.service << SVCEOF
[Unit]
Description=BandMate Audio Player
After=network.target

[Service]
WorkingDirectory=/srv/bandmate
EnvironmentFile=/srv/bandmate/.env
ExecStart=/srv/bandmate/.venv/bin/gunicorn bandmate.wsgi:application \\\\
    --bind ${BIND_ADDR} \\\\
    --workers 3 \\\\
    --timeout 120
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload &&
systemctl enable bandmate &&
systemctl start bandmate
"

# ---------- install Cloudflare Tunnel if enabled ----------------------------
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
    bold "[9/${TOTAL_STEPS}] Installing cloudflared and configuring tunnel..."
    pct exec "$CTID" -- bash -c "
        apt-get install -y -qq curl > /dev/null 2>&1 &&
        curl -fsSL https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb -o /tmp/cloudflared.deb &&
        dpkg -i /tmp/cloudflared.deb > /dev/null 2>&1 &&
        rm -f /tmp/cloudflared.deb
    "

    pct exec "$CTID" -- bash -c "
        cloudflared service install '${CF_TOKEN}'
    "

    echo ""
    echo "  Cloudflare Tunnel connector is running."
fi

# ---------- done -----------------------------------------------------------
sleep 3
if [[ -z "$CT_IP" || "$CT_IP" == "127.0.0.1" ]]; then
    CT_IP=$(pct exec "$CTID" -- bash -c "hostname -I | awk '{print \$1}'" 2>/dev/null || echo "unknown")
fi

echo ""
green "============================================"
green "  BandMate deployed successfully!"
green "============================================"
echo ""
echo "  Container ID : $CTID"
echo "  Container IP : $CT_IP"
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
echo "  URL          : https://${CF_HOSTNAME}"
else
echo "  URL          : http://${CT_IP}:${PORT}"
fi
echo "  Admin user   : $ADMIN_USER"
echo ""
echo "  Manage the service:"
echo "    pct exec $CTID -- systemctl status bandmate"
echo "    pct exec $CTID -- journalctl -u bandmate -f"
if [[ "${ENABLE_TUNNEL,,}" == "y" ]]; then
echo "    pct exec $CTID -- systemctl status cloudflared"
echo "    pct exec $CTID -- journalctl -u cloudflared -f"
echo ""
echo "  NEXT STEP: Go back to the Cloudflare dashboard and click Continue."
echo "  Add a Public Hostname:"
echo "    Subdomain: (e.g. bandmate)  Domain: (e.g. thedeadend.band)"
echo "    Service: HTTP  URL: localhost:${PORT}"
echo "  Save the tunnel. Your site will be live at https://${CF_HOSTNAME}"
fi
echo ""
echo "  Upload songs via the web UI or copy them to:"
echo "    pct exec $CTID -- ls /srv/bandmate/songs/"
echo ""
