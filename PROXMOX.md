# Deploying BandMate on Proxmox

## Prerequisites

- A Proxmox VE host with access to `pct` commands
- Your BandMate code pushed to a Git repository (e.g. GitHub)
- SSH access to your Proxmox host
- (Optional) A Cloudflare account with a domain for HTTPS via Cloudflare Tunnel

## Quick Start

1. Copy `deploy.sh` to your Proxmox host:

   ```bash
   scp deploy.sh root@<proxmox-ip>:/root/deploy.sh
   ```

2. SSH into your Proxmox host and run the script:

   ```bash
   ssh root@<proxmox-ip>
   bash deploy.sh
   ```

3. Follow the interactive prompts.

## What the Script Asks For

| Prompt | Default | Description |
|--------|---------|-------------|
| GitHub repo URL | *(required)* | URL to clone your BandMate repository |
| Container ID | 200 | Proxmox LXC container ID |
| Storage | local-lvm | Proxmox storage backend for the rootfs |
| Disk size | 16 GB | Container disk allocation |
| Admin username | admin | BandMate admin account |
| Admin password | *(required)* | Password for the admin account |
| Port | 80 | Port Gunicorn will listen on |
| Cloudflare Tunnel | No | Optionally set up a Cloudflare Tunnel for HTTPS |
| Public hostname | *(if tunnel)* | Your subdomain (e.g. `bandmate.thedeadend.band`) |
| Tunnel token | *(if tunnel)* | Token from the Cloudflare dashboard |

## What the Script Does

1. **Downloads** the Debian 12 LXC template (if not already cached)
2. **Creates** an unprivileged LXC container (2 cores, 2 GB RAM, 512 MB swap)
3. **Installs** system packages: Python 3, python3-venv, pip, ffmpeg, git, OpenSSH (with root login enabled for SCP)
4. **Clones** your repo into `/srv/bandmate` and installs Python dependencies in a virtualenv
5. **Configures** the environment (`.env` file with secret key, allowed hosts, songs directory)
6. **Initialises** the database (migrations), collects static files, and creates the admin user
7. **Sets up** a systemd service (`bandmate.service`) running Gunicorn
8. **(Optional)** Installs `cloudflared` and registers the Cloudflare Tunnel

## After Deployment

The script prints the container IP and access URL when finished. You can also manage the service with:

```bash
# Check service status
pct exec <CTID> -- systemctl status bandmate

# View live logs
pct exec <CTID> -- journalctl -u bandmate -f

# View Cloudflare Tunnel logs (if enabled)
pct exec <CTID> -- systemctl status cloudflared
pct exec <CTID> -- journalctl -u cloudflared -f

# Restart the service
pct exec <CTID> -- systemctl restart bandmate
```

## Uploading Songs

Upload songs via the BandMate web UI (admin only), or SCP them directly into the container:

```bash
# Copy a single song folder
scp -r "/path/to/Artist - Title" root@<container-ip>:/srv/bandmate/songs/

# Copy all song folders at once
scp -r /path/to/songs/* root@<container-ip>:/srv/bandmate/songs/

# List current songs
ssh root@<container-ip> ls /srv/bandmate/songs/
```

The deploy scripts install OpenSSH server and enable root login automatically. If you need to set or reset the root password:

```bash
# From the Proxmox host
pct enter <CTID>
passwd root
```

## HTTPS with Cloudflare Tunnel

Cloudflare Tunnel creates a secure outbound connection from your LXC container to Cloudflare's network. Cloudflare handles HTTPS termination and routes traffic to your container -- no port forwarding required on your router.

### Step 1: Create the Tunnel in Cloudflare (before running the script)

1. Log in to the [Cloudflare Zero Trust dashboard](https://one.dash.cloudflare.com)
2. Go to **Networks > Tunnels > Create a tunnel**
3. Choose **Cloudflared** as the connector type
4. Name the tunnel (e.g. `bandmate`)
5. Select **Debian** as the operating system and **64-bit** architecture
6. Copy the **tunnel token** -- this is the long string after `--token` in the install command shown on screen
7. **Do not click "Continue" yet** -- the dashboard will show "Waiting for your tunnel to connect..." and that's expected. Leave this browser tab open.

### Step 2: Run the Deploy Script

Run `deploy.sh` on your Proxmox host (see [Quick Start](#quick-start)). When prompted:
- Answer **y** to "Set up Cloudflare Tunnel for HTTPS?"
- Enter your public hostname (e.g. `bandmate.thedeadend.band`)
- Paste the tunnel token you copied from the dashboard

The script will install `cloudflared` in the container and register the tunnel. Once it finishes, the Cloudflare dashboard should detect the connection and let you proceed.

### Step 3: Configure the Public Hostname in Cloudflare (after the script finishes)

1. Go back to the Cloudflare dashboard tab -- the connection status should now show as **Connected**
2. Click **Continue** (or **Next**)
3. Under **Public Hostnames**, add a route:
   - **Subdomain**: `bandmate` (or whatever you prefer)
   - **Domain**: your Cloudflare domain (e.g. `thedeadend.band`)
   - **Service type**: `HTTP`
   - **URL**: `localhost:80` (must match the Gunicorn port you chose)
4. Save the tunnel

Your site should now be live at `https://bandmate.yourdomain.com`.

### How It Works

```
Browser (https://bandmate.thedeadend.band)
   │
   ▼
Cloudflare Edge (handles TLS)
   │
   ▼  (encrypted tunnel, outbound from your network)
cloudflared (in LXC container)
   │
   ▼
Gunicorn (127.0.0.1:80)
```

- **No ports need to be opened** on your router
- Cloudflare provides free HTTPS certificates automatically
- Works even if your ISP blocks ports 80/443
- You can run multiple services on different subdomains without port conflicts
- `SECURE_PROXY_SSL_HEADER` and `CSRF_TRUSTED_ORIGINS` are configured automatically by the script

## Updating

To update BandMate after a new push to your repo:

```bash
pct exec <CTID> -- bash -c "
  cd /srv/bandmate &&
  git pull &&
  source .venv/bin/activate &&
  pip install -r requirements.txt -q &&
  python manage.py migrate --noinput &&
  python manage.py collectstatic --noinput &&
  systemctl restart bandmate
"
```
