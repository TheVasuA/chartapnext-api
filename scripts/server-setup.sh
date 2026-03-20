#!/bin/bash
# Run this ONCE on your Contabo server as root
# Usage: bash server-setup.sh
set -e

echo "=== Chartapnext API — Contabo Server Setup ==="

# 1. Update system
apt-get update && apt-get upgrade -y

# 2. Install Docker
apt-get install -y ca-certificates curl gnupg lsb-release git nginx

# Docker official GPG key
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
chmod a+r /etc/apt/keyrings/docker.gpg

echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | \
  tee /etc/apt/sources.list.d/docker.list > /dev/null

apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# Enable Docker on boot
systemctl enable docker
systemctl start docker

echo ">>> Docker installed: $(docker --version)"

# 3. Create deploy directory
mkdir -p /opt/chartapnext-api
cd /opt/chartapnext-api

# 4. Clone the repo (replace with your repo URL)
REPO_URL="https://github.com/TheVasuA/chartapnext-api.git"
if [ ! -d ".git" ]; then
  git clone "$REPO_URL" .
else
  echo "Repo already cloned."
fi

# 5. Create .env file — fill in your real values!
if [ ! -f ".env" ]; then
  cat > .env <<'EOF'
SECRET_KEY=CHANGE_ME_STRONG_SECRET_KEY_HERE
ALLOWED_ORIGINS=http://109.123.247.224,http://109.123.247.224:3000,http://localhost:3000
DATABASE_URL=postgresql+asyncpg://chartap:chartap@db:5432/chartap
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
EOF
  echo ">>> .env created — EDIT /opt/chartapnext-api/.env with real values before starting!"
fi

# 6. Set up SSH deploy key for GitHub Actions
echo ""
echo "=== GitHub Actions SSH Key Setup ==="
echo "Run this to generate a deploy key:"
echo "  ssh-keygen -t ed25519 -C 'github-actions-deploy' -f ~/.ssh/github_deploy -N ''"
echo "  cat ~/.ssh/github_deploy.pub   # Add to repo: Settings > Deploy keys"
echo "  cat ~/.ssh/github_deploy       # Add to GitHub Secrets as SERVER_SSH_KEY"
echo ""

# 7. Install Nginx config
cat > /etc/nginx/sites-available/chartapnext-api <<'EOF'
server {
    listen 80;
    server_name 109.123.247.224;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
    }
}
EOF

ln -sf /etc/nginx/sites-available/chartapnext-api /etc/nginx/sites-enabled/chartapnext-api
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

echo ""
echo "=== Setup Complete ==="
echo "Next steps:"
echo "  1. Edit /opt/chartapnext-api/.env with real values"
echo "  2. Generate SSH deploy key (see above)"
echo "  3. Add SERVER_SSH_KEY, SERVER_HOST, SERVER_USER, SERVER_PORT to GitHub Secrets"
echo "  4. Run: cd /opt/chartapnext-api && docker compose up -d"
echo "  5. Push to main → GitHub Actions will auto-deploy!"
