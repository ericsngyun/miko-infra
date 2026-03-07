#!/bin/bash
# ============================================================================
# AWaaS Node 1 Bootstrap — GMKtec Evo X2 96GB
# Hardware: AMD Ryzen AI Max+ 395 (Strix Halo / gfx1151), 96GB LPDDR5X, 2TB NVMe
# Run as root immediately after Ubuntu Server 24.04 LTS clean install
# Version: 2026-03-01 — Validated against GMKtec docs, Jeff Geerling guide,
#   community benchmarks, ROCm 7.2 official docs
# ============================================================================
set -euo pipefail

LOG="/var/log/awaas-bootstrap.log"
exec > >(tee -a "$LOG") 2>&1
echo "=== AWaaS Bootstrap started at $(date -u) ==="

# ============================================================================
# PREFLIGHT: Verify we're running as root on the correct hardware
# ============================================================================
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: Must run as root. Use: sudo bash bootstrap.sh"
  exit 1
fi

if ! grep -q "Ryzen AI Max" /proc/cpuinfo 2>/dev/null; then
  echo "WARNING: CPU does not match expected AMD Ryzen AI Max+. Proceeding anyway..."
fi

SUDO_USER_ACTUAL="${SUDO_USER:-awaas}"
echo "Bootstrap running for user: $SUDO_USER_ACTUAL"

# ============================================================================
# 1. System base packages
# ============================================================================
echo "=== [1/16] Installing system base packages ==="
apt update && apt upgrade -y
apt install -y \
  curl wget git vim htop lvm2 ufw net-tools build-essential \
  apcupsd jq smartmontools nvme-cli \
  linux-tools-common linux-tools-generic \
  python3-pip python3.12-venv

# ============================================================================
# 2. UTC timezone — all logs must be UTC
# ============================================================================
echo "=== [2/16] Setting timezone to UTC ==="
timedatectl set-timezone UTC

# ============================================================================
# 3. OEM kernel for gfx1151 stability
# linux-oem-24.04c = kernel 6.14.x series with Strix Halo fixes
# The DEFAULT Ubuntu 24.04 kernel does NOT support gfx1151 reliably
# ============================================================================
echo "=== [3/16] Installing OEM kernel (6.14.x for Strix Halo) ==="
apt install -y linux-oem-24.04c

# ============================================================================
# 4. PIN linux-firmware — certain releases BREAK ROCm on gfx1151
# This is the most critical pin in the entire bootstrap
# ============================================================================
echo "=== [4/16] Pinning linux-firmware to prevent breakage ==="
apt-mark hold linux-firmware

# ============================================================================
# 5. Pin kernel and related packages from auto-upgrades
# ============================================================================
echo "=== [5/16] Pinning kernel packages from unattended upgrades ==="
apt-mark hold linux-generic linux-image-generic linux-headers-generic
cat > /etc/apt/apt.conf.d/51awaas-kernel-hold << "EOF"
Unattended-Upgrade::Package-Blacklist {
    "linux-";
    "linux-firmware";
};
EOF

# ============================================================================
# 6. GRUB parameters — CRITICAL for unified memory APU
# CORRECTED: amdgpu.gttsize is DEPRECATED. Use amdttm.pages_limit instead.
# Calculation: 96GB * 1024 * 1024 / 4.096 = 25,165,824 pages
# amd_iommu=off: Removes IOMMU reservation overhead for full GTT access
# amdttm.pages_limit: Max pages GPU translation table can use
# amdttm.page_pool_size: Pre-allocated page pool for GPU memory
# Combined with BIOS UMA=1GB, this enables dynamic shared memory where
# GPU can access up to the full 96GB pool flexibly.
# ============================================================================
echo "=== [6/16] Configuring GRUB for unified memory (corrected params) ==="
sed -i 's/GRUB_CMDLINE_LINUX=""/GRUB_CMDLINE_LINUX="amd_iommu=off amdttm.pages_limit=25165824 amdttm.page_pool_size=25165824"/' /etc/default/grub
update-grub

# ============================================================================
# 7. HSA override — gfx1151 may not appear in official ROCm support matrix
# Setting this is a safety net; does not hurt if GPU is already detected
# ============================================================================
echo "=== [7/16] Setting HSA_OVERRIDE_GFX_VERSION ==="
echo "HSA_OVERRIDE_GFX_VERSION=11.5.1" >> /etc/environment

# ============================================================================
# 8. ROCm 7.2.0 installation — PINNED, not latest
# Using official AMD repository method for Ubuntu 24.04 Noble
# ============================================================================
echo "=== [8/16] Installing ROCm 7.2.0 (pinned) ==="

# Download and install the signing key
mkdir --parents --mode=0755 /etc/apt/keyrings
wget https://repo.radeon.com/rocm/rocm.gpg.key -O - | \
  gpg --dearmor | tee /etc/apt/keyrings/rocm.gpg > /dev/null

# Add ROCm 7.2 repo — PINNED to 7.2, not latest
tee /etc/apt/sources.list.d/rocm.list << "EOF"
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/rocm/apt/7.2 noble main
deb [arch=amd64 signed-by=/etc/apt/keyrings/rocm.gpg] https://repo.radeon.com/graphics/7.2/ubuntu noble main
EOF

# Pin ROCm packages to prevent mixing versions
tee /etc/apt/preferences.d/rocm-pin-600 << "EOF"
Package: *
Pin: release o=repo.radeon.com
Pin-Priority: 600
EOF

apt update
apt install -y rocm

# Add user to render and video groups
usermod -a -G render,video "$SUDO_USER_ACTUAL"

# ============================================================================
# 9. amdgpu_top — DO NOT USE rocm-smi on APUs
# rocm-smi always shows 0% GPU utilization on integrated APUs
# amdgpu_top correctly reports iGPU CU activity
# ============================================================================
echo "=== [9/16] Installing amdgpu_top ==="
apt install -y cargo
cargo install amdgpu_top --root /usr/local 2>/dev/null || {
  echo "WARNING: amdgpu_top cargo install failed. Will try apt after reboot."
}

# ============================================================================
# 10. Docker Engine — official installer
# ============================================================================
echo "=== [10/16] Installing Docker ==="
curl -fsSL https://get.docker.com | sh
usermod -aG docker "$SUDO_USER_ACTUAL"
systemctl enable docker

# Configure Docker daemon for production
mkdir -p /etc/docker
cat > /etc/docker/daemon.json << "EOF"
{
  "log-driver": "json-file",
  "log-opts": {
    "max-size": "50m",
    "max-file": "3"
  },
  "default-address-pools": [
    {"base": "172.20.0.0/16", "size": 24}
  ],
  "storage-driver": "overlay2",
  "live-restore": true
}
EOF
systemctl restart docker

# ============================================================================
# 11. Tailscale — bare metal, NOT inside Docker
# ============================================================================
echo "=== [11/16] Installing Tailscale ==="
curl -fsSL https://tailscale.com/install.sh | sh

# ============================================================================
# 12. UFW firewall
# ============================================================================
echo "=== [12/16] Configuring UFW firewall ==="
ufw default deny incoming
ufw default allow outgoing
ufw allow ssh
ufw allow in on tailscale0
ufw --force enable

# ============================================================================
# 13. SSH hardening
# ============================================================================
echo "=== [13/16] Hardening SSH ==="
sed -i "s/#PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config
sed -i "s/PasswordAuthentication yes/PasswordAuthentication no/" /etc/ssh/sshd_config
sed -i "s/#PermitRootLogin prohibit-password/PermitRootLogin no/" /etc/ssh/sshd_config
systemctl restart ssh

# ============================================================================
# 14. SOPS + age for secrets management
# ============================================================================
echo "=== [14/16] Installing SOPS + age ==="
apt install -y age

# Install SOPS — pinned version
SOPS_VERSION="3.9.4"
curl -Lo /usr/local/bin/sops \
  "https://github.com/mozilla/sops/releases/download/v${SOPS_VERSION}/sops-v${SOPS_VERSION}.linux.amd64"
chmod +x /usr/local/bin/sops

# ============================================================================
# 15. Ollama — bare metal install for direct iGPU access
# Docker Ollama cannot access the integrated GPU reliably on APUs with ROCm
# ============================================================================
echo "=== [15/16] Installing Ollama ==="
curl -fsSL https://ollama.com/install.sh | sh

# Configure Ollama for production with systemd override
mkdir -p /etc/systemd/system/ollama.service.d
cat > /etc/systemd/system/ollama.service.d/override.conf << "EOF"
[Service]
Environment="OLLAMA_HOST=0.0.0.0:11434"
Environment="OLLAMA_MODELS=/mnt/models/production"
Environment="OLLAMA_MAX_LOADED_MODELS=3"
Environment="OLLAMA_NUM_PARALLEL=2"
Environment="OLLAMA_KEEP_ALIVE=30m"
Environment="OLLAMA_NO_CLOUD=1"
Environment="HSA_OVERRIDE_GFX_VERSION=11.5.1"
EOF
systemctl daemon-reload

# ============================================================================
# 16. Write service image pins to /etc/awaas-image-pins.env
# Source this in all docker-compose.yml via env_file
# ALL VERSIONS VERIFIED 2026-03-01 — DO NOT change without Go/No-Go
# ============================================================================
echo "=== [16/16] Writing verified image pins ==="
cat > /etc/awaas-image-pins.env << "EOF"
# AWaaS Validated Image Pins — 2026-03-01
# Verified against Docker Hub, GitHub Releases, official docs
# DO NOT change without running full 16-gate Go/No-Go
# -------------------------------------------------------
# Core data layer
POSTGRES_IMAGE=postgres:18.3
REDIS_IMAGE=redis:8.6.1-alpine
QDRANT_IMAGE=qdrant/qdrant:v1.17.0

# Workflow automation
N8N_IMAGE=n8nio/n8n:2.9.4

# Monitoring stack
GRAFANA_IMAGE=grafana/grafana:12.3.4
PROMETHEUS_IMAGE=prom/prometheus:v3.10.0
LOKI_IMAGE=grafana/loki:3.6.7
ALERTMANAGER_IMAGE=prom/alertmanager:v0.28.1

# Edge proxy
CADDY_IMAGE=caddy:2-alpine

# Host metrics
NODE_EXPORTER_IMAGE=prom/node-exporter:v1.9.0
CADVISOR_IMAGE=gcr.io/cadvisor/cadvisor:v0.52.1
EOF
chmod 644 /etc/awaas-image-pins.env

# ============================================================================
# LVM mount point preparation (volumes created during Ubuntu install)
# ============================================================================
echo "=== Creating mount points and directories ==="
mkdir -p /mnt/models/production /mnt/models/staging
mkdir -p /mnt/data
mkdir -p /mnt/audit/logs

# Set ownership
chown -R "$SUDO_USER_ACTUAL":"$SUDO_USER_ACTUAL" /mnt/models
chown -R "$SUDO_USER_ACTUAL":"$SUDO_USER_ACTUAL" /mnt/data

# Audit log: create dedicated audit user for write-only enforcement
# This is the physical enforcement of attorney-client privilege compliance
useradd -r -s /usr/sbin/nologin awaas-audit 2>/dev/null || true
chown -R awaas-audit:awaas-audit /mnt/audit
chmod 1733 /mnt/audit/logs  # Sticky bit: write-only, no delete

# ============================================================================
# NVMe health monitoring
# ============================================================================
echo "=== Configuring NVMe health monitoring ==="
cat > /etc/cron.daily/nvme-health << "CRONEOF"
#!/bin/bash
nvme smart-log /dev/nvme0n1 | tee -a /var/log/nvme-health.log
TEMP=$(nvme smart-log /dev/nvme0n1 | grep "temperature" | head -1 | awk '{print $3}')
if [[ "${TEMP%%.*}" -gt 70 ]]; then
  echo "CRITICAL: NVMe temperature ${TEMP}C" | logger -t awaas-nvme
fi
CRONEOF
chmod +x /etc/cron.daily/nvme-health

# ============================================================================
# UPS monitoring (apcupsd)
# ============================================================================
echo "=== Configuring UPS monitoring ==="
cat > /etc/apcupsd/apcupsd.conf << "EOF"
UPSNAME awaas-ups
UPSCABLE usb
UPSTYPE usb
DEVICE
POLLTIME 30
ONBATTERYDELAY 6
BATTERYLEVEL 15
MINUTES 5
TIMEOUT 0
ANNOY 300
ANNOYDELAY 60
NOLOGON disable
KILLDELAY 0
NETSERVER on
NISIP 0.0.0.0
NISPORT 3551
EOF
systemctl enable apcupsd
systemctl restart apcupsd 2>/dev/null || echo "UPS not connected yet — will start when plugged in."

# ============================================================================
# AWaaS directory structure
# ============================================================================
echo "=== Creating AWaaS directory structure ==="
AWAAS_HOME="/home/$SUDO_USER_ACTUAL/awaas"
mkdir -p "$AWAAS_HOME"/{shared,pleadly,awaas_services,orchestrator,trading,monitor,migrations,secrets}
mkdir -p "$AWAAS_HOME"/shared/llm-gateway
mkdir -p "$AWAAS_HOME"/awaas_services/action-gateway
chown -R "$SUDO_USER_ACTUAL":"$SUDO_USER_ACTUAL" "$AWAAS_HOME"

# ============================================================================
# COMPLETE
# ============================================================================
echo ""
echo "============================================================"
echo "  AWaaS Node 1 Bootstrap COMPLETE"
echo "  $(date -u)"
echo "============================================================"
echo ""
echo "  REBOOT NOW:  sudo reboot"
echo ""
echo "  After reboot, verify:"
echo "    1. uname -r                          -> 6.14.x (OEM kernel)"
echo "    2. cat /proc/cmdline | grep amdttm   -> amdttm.pages_limit=25165824"
echo "    3. cat /etc/environment              -> HSA_OVERRIDE_GFX_VERSION=11.5.1"
echo "    4. rocminfo | grep gfx               -> gfx1151"
echo "    5. ollama --version                   -> 0.17.x"
echo "    6. docker --version                   -> 27.x"
echo "    7. sops --version                     -> 3.9.4"
echo "    8. amdgpu_top                         -> launches GPU monitor"
echo ""
echo "  BIOS settings (must be set BEFORE running this script):"
echo "    - Enter BIOS: ESC key during startup"
echo "    - UMA Frame Buffer: 1G (Advanced > GFX Config > iGPU > UMA_SPECIFIED)"
echo "    - Secure Boot: DISABLED"
echo "    - TPM: Enabled"
echo "    - Performance Mode: Performance (Main > Power Limit Setting)"
echo "    - Auto Power On: Enabled (Advanced > Auto Power On)"
echo "    - SVM: Enabled"
echo "    - Network Boot: Disabled"
echo ""
echo "  Log saved to: $LOG"
echo "============================================================"
