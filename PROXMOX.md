# Deploying BandMate on Proxmox

## Prerequisites

- A Proxmox VE host
- Your BandMate code pushed to a Git repository (e.g. GitHub)
- (Optional) A Cloudflare account with a domain for HTTPS via Cloudflare Tunnel

## Step 1: Create the LXC Container

### Option A: Proxmox Web UI

1. Log in to your Proxmox web interface
2. Click **Create CT** in the top-right corner
3. Configure the container:
   - **General**: Pick a Container ID (e.g. `200`), set a hostname (e.g. `bandmate`), and set a root password
   - **Template**: Select a **Debian 12** standard template (download one first via **local > CT Templates > Templates** if needed)
   - **Disks**: Root disk size **16 GB** (adjust based on how many songs you plan to store)
   - **CPU**: **2 cores**
   - **Memory**: **2048 MB** RAM, **512 MB** swap
   - **Network**: Bridge `vmbr0`, IPv4 **DHCP** (or a static IP if you prefer)
4. Click **Finish** and start the container

### Option B: Proxmox CLI

```bash
# Download the latest Debian 12 template if you don't have one
pveam update
TEMPLATE=$(pveam available --section system | grep -o 'debian-12-standard[^ ]*' | tail -1)
pveam download local "$TEMPLATE"

# Create the container
pct create 200 local:vztmpl/$TEMPLATE \
    --hostname bandmate \
    --rootfs local-lvm:16 \
    --cores 2 \
    --memory 2048 \
    --swap 512 \
    --net0 name=eth0,bridge=vmbr0,ip=dhcp \
    --unprivileged 1 \
    --features nesting=1 \
    --start 0

# Start it
pct start 200
```

## Step 2: Set the Root Password

If you haven't already set one during creation, or need to reset it:

```bash
pct enter 200
passwd root
exit
```

## Step 3: Copy and Run deploy-service.sh

Find your container's IP address:

```bash
pct exec 200 -- hostname -I
```

Copy the deploy script into the container and run it:

```bash
scp deploy-service.sh root@<container-ip>:/root/deploy-service.sh
ssh root@<container-ip>
bash deploy-service.sh
```

Follow the interactive prompts.

### What the Script Asks For

| Prompt | Default | Description |
|--------|---------|-------------|
| GitHub repo URL | *(required)* | URL to clone your BandMate repository |
| Admin username | admin | BandMate admin account |
| Admin password | *(required)* | Password for the admin account |
| Port | 80 | Port Gunicorn will listen on |
| Cloudflare Tunnel | No | Optionally set up a Cloudflare Tunnel for HTTPS |
| Public hostname | *(if tunnel)* | Your subdomain (e.g. `bandmate.thedeadend.band`) |
| Tunnel token | *(if tunnel)* | Token from the Cloudflare dashboard |

### What the Script Does

1. **Installs** system packages: Python 3, python3-venv, pip, ffmpeg, git, OpenSSH (with root login enabled for SCP)
2. **Clones** your repo into `/srv/bandmate` and installs Python dependencies in a virtualenv
3. **Configures** the environment (`.env` file with secret key, allowed hosts, songs/cache directories)
4. **Initialises** the database (migrations), collects static files, and creates the admin user
5. **Sets up** a systemd service (`bandmate.service`) running Gunicorn
6. **(Optional)** Installs `cloudflared` and registers the Cloudflare Tunnel

## After Deployment

The script prints the container IP and access URL when finished. You can also manage the service with:

```bash
ssh root@<container-ip> systemctl status bandmate
ssh root@<container-ip> journalctl -u bandmate -f
ssh root@<container-ip> systemctl restart bandmate
```

Or from the Proxmox host:

```bash
pct exec 200 -- systemctl status bandmate
pct exec 200 -- journalctl -u bandmate -f
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

Run `deploy-service.sh` inside your container (see [Step 3](#step-3-copy-and-run-deploy-servicesh) above). When prompted:
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
ssh root@<container-ip> bash -c "
  cd /srv/bandmate &&
  git pull &&
  source .venv/bin/activate &&
  pip install -r requirements.txt -q &&
  python manage.py migrate --noinput &&
  python manage.py collectstatic --noinput &&
  systemctl restart bandmate
"
```
