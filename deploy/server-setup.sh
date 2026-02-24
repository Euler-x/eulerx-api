#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
# EulerX Server Setup Script
# Run this as root on a fresh Ubuntu 22.04/24.04 server
# ═══════════════════════════════════════════════════════════

DEPLOY_USER="eulerx"
DEPLOY_PASS='QazWsx.!@#.123'
APP_DIR="/home/${DEPLOY_USER}/eulerx-api"
REPO_URL="https://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@github.com/Euler-x/eulerx-api.git"
PYTHON_VERSION="3.11"

echo "═══════════════════════════════════════════"
echo "  EulerX Server Setup"
echo "═══════════════════════════════════════════"

# ── 1. System Update ──────────────────────────────────────
echo "[1/9] Updating system packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get upgrade -y
apt-get dist-upgrade -y

# ── 2. Create deploy user ────────────────────────────────
echo "[2/9] Creating user: ${DEPLOY_USER}..."
if id "${DEPLOY_USER}" &>/dev/null; then
    echo "  User ${DEPLOY_USER} already exists, skipping..."
else
    useradd -m -s /bin/bash "${DEPLOY_USER}"
    echo "${DEPLOY_USER}:${DEPLOY_PASS}" | chpasswd
    usermod -aG sudo "${DEPLOY_USER}"
    echo "${DEPLOY_USER} ALL=(ALL) NOPASSWD:ALL" > /etc/sudoers.d/${DEPLOY_USER}
    chmod 0440 /etc/sudoers.d/${DEPLOY_USER}
    echo "  User ${DEPLOY_USER} created with sudo access."
fi

# ── 3. Set up SSH key for deploy user ────────────────────
echo "[3/9] Setting up SSH key for CI/CD..."
DEPLOY_SSH_DIR="/home/${DEPLOY_USER}/.ssh"
mkdir -p "${DEPLOY_SSH_DIR}"
cat > "${DEPLOY_SSH_DIR}/authorized_keys" << 'PUBKEY'
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOf1PTWA5EwSawqYKwYjjVYodhCVGq4yLrFbfCB0NUSl eulerx-deploy
PUBKEY
chmod 700 "${DEPLOY_SSH_DIR}"
chmod 600 "${DEPLOY_SSH_DIR}/authorized_keys"
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" "${DEPLOY_SSH_DIR}"
echo "  SSH key added for CI/CD deployment."

# ── 4. Install system dependencies ───────────────────────
echo "[4/9] Installing system dependencies..."
apt-get install -y \
    software-properties-common \
    build-essential \
    python${PYTHON_VERSION} \
    python${PYTHON_VERSION}-venv \
    python${PYTHON_VERSION}-dev \
    python3-pip \
    nginx \
    redis-server \
    postgresql-client \
    git \
    curl \
    wget \
    htop \
    ufw \
    certbot \
    python3-certbot-nginx \
    supervisor

# Ensure python3 points to 3.11
update-alternatives --install /usr/bin/python3 python3 /usr/bin/python${PYTHON_VERSION} 1 2>/dev/null || true

# ── 5. Install uv (Python package manager) ──────────────
echo "[5/9] Installing uv package manager..."
if ! command -v uv &>/dev/null; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="/root/.local/bin:$PATH"
    # Also install for deploy user
    su - "${DEPLOY_USER}" -c 'curl -LsSf https://astral.sh/uv/install.sh | sh'
fi
echo "  uv installed."

# ── 6. Configure Redis ───────────────────────────────────
echo "[6/9] Configuring Redis..."
systemctl enable redis-server
systemctl start redis-server
echo "  Redis running."

# ── 7. Configure Firewall ────────────────────────────────
echo "[7/9] Configuring firewall..."
ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable
echo "  Firewall configured."

# ── 8. Clone repository ─────────────────────────────────
echo "[8/9] Cloning repository..."
if [ -d "${APP_DIR}" ]; then
    echo "  Repository already exists, pulling latest..."
    su - "${DEPLOY_USER}" -c "cd ${APP_DIR} && git pull"
else
    su - "${DEPLOY_USER}" -c "git clone ${REPO_URL} ${APP_DIR}"
fi

# ── 9. Set up Python environment ─────────────────────────
echo "[9/9] Setting up Python environment..."
su - "${DEPLOY_USER}" -c "
    cd ${APP_DIR}/backend
    export PATH=\"\$HOME/.local/bin:\$PATH\"
    uv venv .venv --python python${PYTHON_VERSION}
    source .venv/bin/activate
    uv sync --no-dev
    echo 'Python venv ready.'
"

# ── Create app directories ───────────────────────────────
mkdir -p /var/log/eulerx
chown -R "${DEPLOY_USER}:${DEPLOY_USER}" /var/log/eulerx

echo ""
echo "═══════════════════════════════════════════"
echo "  Server setup complete!"
echo "═══════════════════════════════════════════"
echo ""
echo "Next steps:"
echo "  1. Copy .env to ${APP_DIR}/backend/.env"
echo "  2. Run: sudo cp ${APP_DIR}/deploy/*.service /etc/systemd/system/"
echo "  3. Run: sudo cp ${APP_DIR}/deploy/nginx-eulerx.conf /etc/nginx/sites-available/eulerx"
echo "  4. Run: sudo ln -sf /etc/nginx/sites-available/eulerx /etc/nginx/sites-enabled/"
echo "  5. Run: sudo rm -f /etc/nginx/sites-enabled/default"
echo "  6. Run: sudo systemctl daemon-reload"
echo "  7. Run: sudo systemctl enable --now eulerx-api eulerx-celery eulerx-beat"
echo "  8. Run: sudo systemctl restart nginx"
echo ""
