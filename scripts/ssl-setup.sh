#!/bin/bash
# Run this on your Contabo server AFTER server-setup.sh
# PREREQUISITE: Add DNS A record → api.chartap.com → 109.123.247.224
# Usage: bash /opt/chartapnext-api/scripts/ssl-setup.sh
set -e

DOMAIN="api.chartap.com"
EMAIL="your@email.com"   # ← change to your real email for cert renewal alerts

echo "================================================"
echo " SSL Setup for $DOMAIN"
echo " (Let's Encrypt — free, auto-renews)"
echo "================================================"
echo ""

# 1. Install certbot
echo "[1/3] Installing certbot..."
apt-get update -y
apt-get install -y certbot python3-certbot-nginx

# 2. Get SSL certificate
echo "[2/3] Obtaining SSL certificate for $DOMAIN..."
certbot --nginx \
  -d "$DOMAIN" \
  --non-interactive \
  --agree-tos \
  --email "$EMAIL" \
  --redirect

# 3. Update nginx config to also add WebSocket (wss://) headers
echo "[3/3] Updating nginx config with WebSocket support..."
cat > /etc/nginx/sites-available/chartapnext-api <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    return 301 https://\$host\$request_uri;
}

server {
    listen 443 ssl;
    server_name $DOMAIN;

    ssl_certificate     /etc/letsencrypt/live/$DOMAIN/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/$DOMAIN/privkey.pem;
    include             /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam         /etc/letsencrypt/ssl-dhparams.pem;

    # Security headers
    add_header X-Frame-Options SAMEORIGIN;
    add_header X-Content-Type-Options nosniff;
    add_header X-XSS-Protection "1; mode=block";
    add_header Access-Control-Allow-Origin "https://www.chartap.com" always;
    add_header Access-Control-Allow-Methods "GET, POST, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Content-Type, Authorization" always;

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

nginx -t && systemctl reload nginx

echo ""
echo "================================================"
echo " SSL Setup Complete!"
echo "================================================"
echo ""
echo "Your API is now available at:"
echo "  https://$DOMAIN/docs"
echo "  https://$DOMAIN/signals"
echo "  https://$DOMAIN/smc/"
echo ""
echo "WebSocket:"
echo "  wss://$DOMAIN/ws/signals"
echo ""
echo "Auto-renewal is handled by certbot systemd timer."
echo "Check: systemctl status certbot.timer"
