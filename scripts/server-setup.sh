#!/bin/bash
# Run this ONCE on your Contabo server as root
# Usage: bash server-setup.sh
set -e

SERVER_IP="109.123.247.224"
REPO_URL="https://github.com/TheVasuA/chartapnext-api.git"
DEPLOY_DIR="/opt/chartapnext-api"

echo "================================================"
echo " Chartapnext API — Contabo Server Setup"
echo " Server: $SERVER_IP"
echo "================================================"
echo ""

# ──────────────────────────────────────────────
# 1. UBUNTU — Full system update & hardening
# ──────────────────────────────────────────────
echo "[1/7] Updating Ubuntu..."
apt-get update -y
apt-get upgrade -y
apt-get dist-upgrade -y
apt-get autoremove -y
apt-get autoclean -y

echo ">>> Ubuntu updated: $(lsb_release -d | cut -f2)"

# Enable automatic security updates
echo "[1b] Enabling unattended security upgrades..."
apt-get install -y unattended-upgrades
cat > /etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
APT::Periodic::AutocleanInterval "7";
EOF
echo ">>> Auto security upgrades enabled"

# ──────────────────────────────────────────────
# 2. FIREWALL — Allow only needed ports
# ──────────────────────────────────────────────
echo "[2/7] Configuring UFW firewall..."
apt-get install -y ufw
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp    # SSH
ufw allow 80/tcp    # HTTP (nginx → api)
ufw allow 443/tcp   # HTTPS (future SSL)
ufw --force enable
echo ">>> UFW status:"
ufw status verbose

# ──────────────────────────────────────────────
# 3. INSTALL — Required packages
# ──────────────────────────────────────────────
echo "[3/7] Installing dependencies..."
apt-get install -y \
  ca-certificates curl gnupg lsb-release \
  git nginx htop net-tools

# ──────────────────────────────────────────────
# 4. DOCKER — Install latest stable
# ──────────────────────────────────────────────
echo "[4/7] Installing Docker..."

# Remove old docker versions if any
for pkg in docker.io docker-doc docker-compose podman-docker containerd runc; do
  apt-get remove -y $pkg 2>/dev/null || true
done

# Add Docker's official GPG key
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

# Add Docker apt repository
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y \
  docker-ce \
  docker-ce-cli \
  containerd.io \
  docker-buildx-plugin \
  docker-compose-plugin

# Enable and start Docker
systemctl enable docker
systemctl start docker

echo ">>> Docker installed: $(docker --version)"
echo ">>> Docker Compose installed: $(docker compose version)"

# ──────────────────────────────────────────────
# 5. CLONE — Deploy directory & repo
# ──────────────────────────────────────────────
echo "[5/7] Setting up deploy directory..."
mkdir -p $DEPLOY_DIR
cd $DEPLOY_DIR

if [ ! -d ".git" ]; then
  git clone "$REPO_URL" .
  echo ">>> Repo cloned"
else
  git pull origin main
  echo ">>> Repo updated"
fi

# ──────────────────────────────────────────────
# 6. ENV — Create .env template
# ──────────────────────────────────────────────
echo "[6/7] Creating .env template..."
if [ ! -f ".env" ]; then
  cat > .env <<EOF
SECRET_KEY=CHANGE_ME_STRONG_SECRET_KEY_HERE
ALLOWED_ORIGINS=http://${SERVER_IP},http://${SERVER_IP}:3000,http://localhost:3000
DATABASE_URL=postgresql+asyncpg://chartap:chartap@db:5432/chartap
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
EOF
  echo ">>> .env created — EDIT $DEPLOY_DIR/.env before starting containers!"
else
  echo ">>> .env already exists — skipping"
fi

# ──────────────────────────────────────────────
# 7. NGINX — Reverse proxy config
# ──────────────────────────────────────────────
echo "[7/7] Configuring Nginx reverse proxy..."
cat > /etc/nginx/sites-available/chartapnext-api <<EOF
server {
    listen 80;
    server_name ${SERVER_IP};

    # Security headers
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        client_max_body_size 10M;
    }
}
EOF

ln -sf /etc/nginx/sites-available/chartapnext-api /etc/nginx/sites-enabled/chartapnext-api
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl enable nginx && systemctl reload nginx
echo ">>> Nginx configured"

# ──────────────────────────────────────────────
# DONE
# ──────────────────────────────────────────────
echo ""
echo "================================================"
echo " Setup Complete!"
echo "================================================"
echo ""
echo "NEXT STEPS:"
echo ""
echo "  1. Edit the .env file with real values:"
echo "       nano $DEPLOY_DIR/.env"
echo "       (change SECRET_KEY to something random)"
echo ""
echo "  2. Generate SSH key for GitHub Actions auto-deploy:"
echo "       ssh-keygen -t ed25519 -C 'github-actions' -f ~/.ssh/github_deploy -N ''"
echo "       cat ~/.ssh/github_deploy.pub  → GitHub repo > Settings > Deploy keys"
echo "       cat ~/.ssh/github_deploy      → GitHub repo > Settings > Secrets > SERVER_SSH_KEY"
echo ""
echo "  3. Add these GitHub Secrets:"
echo "       SERVER_HOST = $SERVER_IP"
echo "       SERVER_USER = root"
echo "       SERVER_PORT = 22"
echo "       SERVER_SSH_KEY = (private key from step 2)"
echo ""
echo "  4. Start the API:"
echo "       cd $DEPLOY_DIR && docker compose up -d"
echo ""
echo "  5. Verify:"
echo "       curl http://$SERVER_IP/docs"
echo ""
echo "After step 2-3, every git push to main auto-deploys!"

