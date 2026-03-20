#!/bin/bash
# Run this on your Contabo server to update Ubuntu + Docker + redeploy
# Usage: bash /opt/chartapnext-api/scripts/update.sh
set -e

DEPLOY_DIR="/opt/chartapnext-api"

echo "================================================"
echo " Chartapnext API — Server Update"
echo " $(date)"
echo "================================================"
echo ""

# ──────────────────────────────────────────────────
# 1. UBUNTU — Full system update
# ──────────────────────────────────────────────────
echo "[1/4] Updating Ubuntu packages..."
apt-get update -y
apt-get upgrade -y
apt-get dist-upgrade -y
apt-get autoremove -y
apt-get autoclean -y
echo ">>> Ubuntu updated: $(lsb_release -d | cut -f2)"

# ──────────────────────────────────────────────────
# 2. DOCKER — Update to latest stable
# ──────────────────────────────────────────────────
echo "[2/4] Updating Docker..."
apt-get install -y --only-upgrade \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

systemctl restart docker
echo ">>> Docker updated: $(docker --version)"
echo ">>> Docker Compose: $(docker compose version)"

# ──────────────────────────────────────────────────
# 3. APP — Pull latest code & rebuild containers
# ──────────────────────────────────────────────────
echo "[3/4] Pulling latest code from GitHub..."
cd $DEPLOY_DIR
git pull origin main

echo ">>> Rebuilding and restarting containers..."
docker compose up --build -d --remove-orphans

echo ">>> Cleaning unused Docker images..."
docker image prune -f

# ──────────────────────────────────────────────────
# 4. STATUS — Show running containers
# ──────────────────────────────────────────────────
echo "[4/4] Checking container health..."
docker compose ps

echo ""
echo "================================================"
echo " Update Complete!"
echo "================================================"
echo ""
echo "API:  http://109.123.247.224/docs"
echo "Test: curl http://109.123.247.224/signals"
