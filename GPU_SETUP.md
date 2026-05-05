# GPU Setup for BandMate (Proxmox LXC)

This guide walks through enabling NVIDIA GPU acceleration for stem separation
inside a Proxmox LXC container.

---

## Prerequisites

- Proxmox VE host with an NVIDIA GPU installed
- An existing BandMate LXC container

---

## 1. Install NVIDIA Drivers on the Proxmox Host

### Enable non-free repositories

Edit `/etc/apt/sources.list` and ensure your Debian repo lines include
`non-free non-free-firmware`:

```
deb http://deb.debian.org/debian bookworm main contrib non-free non-free-firmware
```

### Install kernel headers and driver

```bash
apt update
apt install pve-headers-$(uname -r)
apt install nvidia-driver
```

### Disable Secure Boot

If `modprobe nvidia` fails with "Key was rejected by service", Secure Boot is
blocking the unsigned kernel module. Reboot into BIOS/UEFI and **disable
Secure Boot**.

### Verify

After rebooting:

```bash
nvidia-smi
```

You should see your GPU listed. Note the **driver version** (e.g., `535.261.03`)
and the **CUDA version** — you'll need both later.

---

## 2. Configure GPU Passthrough to the LXC Container

### Get device major numbers

On the host:

```bash
ls -la /dev/nvidia*
```

Note the major numbers (the first number after `root root`). Typical values:

| Device | Major |
|--------|-------|
| `/dev/nvidia0`, `/dev/nvidiactl` | 195 |
| `/dev/nvidia-uvm`, `/dev/nvidia-uvm-tools` | 509 (varies) |
| `/dev/nvidia-caps/` | 236 (varies) |

### Edit the LXC config

On the host, edit `/etc/pve/lxc/<CTID>.conf` (replace `<CTID>` with your
container ID):

```
lxc.cgroup2.devices.allow: c 195:* rwm
lxc.cgroup2.devices.allow: c <uvm-major>:* rwm
lxc.cgroup2.devices.allow: c <caps-major>:* rwm
lxc.mount.entry: /dev/nvidia0 dev/nvidia0 none bind,optional,create=file
lxc.mount.entry: /dev/nvidiactl dev/nvidiactl none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm dev/nvidia-uvm none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-uvm-tools dev/nvidia-uvm-tools none bind,optional,create=file
lxc.mount.entry: /dev/nvidia-caps dev/nvidia-caps none bind,optional,create=dir
```

Replace `<uvm-major>` and `<caps-major>` with the actual major numbers from
`ls -la /dev/nvidia*`.

**Important:**
- Only include devices that actually exist on your host. If `/dev/nvidia-modeset`
  doesn't exist, don't add it (it's only needed for display output, not compute).
- **Each `lxc.mount.entry` line must be on a single line.** Do not let the line
  wrap or break — a broken line (e.g., `bind,optional,create=file` on a separate
  line) will cause a "failed to mount" error on container start.

### Restart the container

```bash
pct stop <CTID>
pct start <CTID>
```

### Verify devices inside the container

```bash
pct enter <CTID>
ls -la /dev/nvidia*
```

You should see the NVIDIA device files.

---

## 3. Install NVIDIA Userspace Driver Inside the Container

The container shares the host's kernel modules, but needs the matching userspace
libraries and tools.

### Check the driver version on the host

```bash
nvidia-smi | head -3
```

Note the version (e.g., `535.261.03`).

### Download and install inside the container

```bash
apt install wget build-essential -y
wget https://us.download.nvidia.com/XFree86/Linux-x86_64/<VERSION>/NVIDIA-Linux-x86_64-<VERSION>.run
chmod +x NVIDIA-Linux-x86_64-<VERSION>.run
./NVIDIA-Linux-x86_64-<VERSION>.run --no-kernel-modules
```

Replace `<VERSION>` with the exact version from the host.

When prompted:
- **Install 32-bit compatibility libraries?** → No
- **Run nvidia-xconfig?** → No

### Verify

```bash
nvidia-smi
```

You should see your GPU listed inside the container.

---

## 4. Install GPU Python Packages for BandMate

```bash
source /srv/bandmate/.venv/bin/activate
```

### Install onnxruntime-gpu and CUDA libraries

```bash
pip uninstall onnxruntime -y
pip install onnxruntime-gpu
pip install -r requirements-gpu.txt
```

Verify ONNX can see the GPU:

```bash
python3 -c "import onnxruntime; print(onnxruntime.get_available_providers())"
```

Expected output includes `'CUDAExecutionProvider'`. A warning about
`/sys/class/drm/card0` is harmless and can be ignored.

### Install PyTorch with CUDA

The stem separation models (RoFormer, htdemucs) use PyTorch, not ONNX. You
must install a CUDA-enabled PyTorch build. This requires a special index URL
and cannot be automated via `requirements.txt`.

For **driver 535.x** (CUDA 12.2 or older):

```bash
pip uninstall torch torchaudio -y
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
```

For **driver 550+** (CUDA 12.4):

```bash
pip uninstall torch torchaudio -y
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu124
```

Verify PyTorch can see the GPU:

```bash
python3 -c "import torch; print(torch.cuda.is_available()); print(torch.cuda.get_device_name(0))"
```

Should output `True` and your GPU name.

### Restart BandMate

```bash
systemctl restart bandmate
```

`audio-separator` will now use the GPU for stem separation. You can confirm by
checking `nvidia-smi` while a job is processing — the python process should
appear with GPU memory allocated.

---

## Troubleshooting

### "nvidia-smi has failed because it couldn't communicate with the NVIDIA driver"

On the host:
```bash
modprobe nvidia
```

If it fails with "Key was rejected by service" → disable Secure Boot in BIOS.

### "failed to mount /dev/nvidia-uvm-tools"

1. Verify the device exists on the host: `ls -la /dev/nvidia-uvm-tools`
2. If it doesn't exist, run `nvidia-smi` on the host to initialize UVM devices,
   or remove the line from the LXC config.
3. Check that each `lxc.mount.entry` line is on a **single line** with no
   line breaks.

### "An NVIDIA kernel module 'nvidia-uvm' appears to already be loaded"

Use the `--no-kernel-modules` flag (plural) when running the installer inside
the container. The container shares the host kernel — it cannot load or unload
kernel modules.

### "detected dubious ownership in repository"

If you changed the container's privilege level, git may complain about
directory ownership. Fix with:

```bash
git config --global --add safe.directory /srv/bandmate
```

### nvidia-smi shows CUDA 12.x but onnxruntime-gpu fails

Make sure you're installing the correct version:
- CUDA 12.x → `pip install onnxruntime-gpu` (latest)
- CUDA 11.x → `pip install onnxruntime-gpu==1.18.1`

### PyTorch says "The NVIDIA driver on your system is too old"

This means the PyTorch CUDA build requires a newer driver than what you have.
- Driver 535.x → use `cu118` index URL
- Driver 550+ → use `cu121` or `cu124` index URL

### update-service.sh overwrites GPU packages

The update script runs `pip install -r requirements.txt` which may pull in
CPU-only torch (as a dependency of `audio-separator`). After running the update
script on a GPU server, re-run:

```bash
source /srv/bandmate/.venv/bin/activate
pip install -r requirements-gpu.txt
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu118
```

### Host reboot loses /dev/nvidia-uvm devices

Add a cron job on the host to initialize devices at boot:

```bash
crontab -e
```

Add:

```
@reboot /usr/bin/nvidia-smi
@reboot /usr/bin/nvidia-persistenced
```
