#!/usr/bin/env bash
set -euo pipefail

# ═══════════════════════════════════════════════════════════
# EulerX Deploy Script
# Called by CI/CD after SSH into the server
# ═══════════════════════════════════════════════════════════

APP_DIR="/home/eulerx/eulerx-api"
BACKEND_DIR="${APP_DIR}/backend"
DEPLOY_DIR="${APP_DIR}/deploy"
PYTHON_VERSION="3.11"

echo "═══════════════════════════════════════════"
echo "  EulerX Deployment"
echo "═══════════════════════════════════════════"

# ── 1. Pull latest code ────────────────────────────────────
echo "[1/6] Pulling latest code..."
cd "${APP_DIR}"
git fetch origin main
git reset --hard origin/main

# ── 2. Install/update dependencies ────────────────────────
echo "[2/6] Installing dependencies..."
cd "${BACKEND_DIR}"
export PATH="$HOME/.local/bin:$PATH"
uv sync --no-dev

# ── 3. Run database migrations ─────────────────────────────
echo "[3/6] Running database migrations..."
cd "${BACKEND_DIR}"
source .venv/bin/activate
alembic upgrade head
deactivate

# ── 4. Copy service files ──────────────────────────────────
echo "[4/6] Updating service files..."
sudo cp "${DEPLOY_DIR}/eulerx-api.service" /etc/systemd/system/
sudo cp "${DEPLOY_DIR}/eulerx-celery.service" /etc/systemd/system/
sudo cp "${DEPLOY_DIR}/eulerx-beat.service" /etc/systemd/system/
sudo cp "${DEPLOY_DIR}/nginx-eulerx.conf" /etc/nginx/sites-available/eulerx
sudo ln -sf /etc/nginx/sites-available/eulerx /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default
sudo systemctl daemon-reload

# ── 5. Restart services ───────────────────────────────────
echo "[5/6] Restarting services..."
sudo systemctl restart eulerx-api
sudo systemctl restart eulerx-celery
sudo systemctl restart eulerx-beat
sudo nginx -t && sudo systemctl restart nginx

# ── 6. Verify services are running ─────────────────────────
echo "[6/6] Verifying services..."
sleep 3

SERVICES=("eulerx-api" "eulerx-celery" "eulerx-beat" "nginx" "redis-server")
ALL_OK=true

for svc in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$svc"; then
        echo "  ✓ $svc is running"
    else
        echo "  ✗ $svc is NOT running"
        ALL_OK=false
    fi
done

if [ "$ALL_OK" = true ]; then
    echo ""
    echo "═══════════════════════════════════════════"
    echo "  Deployment successful!"
    echo "═══════════════════════════════════════════"
else
    echo ""
    echo "WARNING: Some services failed to start. Check logs:"
    echo "  sudo journalctl -u eulerx-api -n 50"
    echo "  sudo journalctl -u eulerx-celery -n 50"
    echo "  sudo journalctl -u eulerx-beat -n 50"
    exit 1
fi
