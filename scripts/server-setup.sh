#!/bin/bash
# SSL Setup for Chartapnext API
# Usage:  bash ssl-setup.sh [domain.com]   → Let's Encrypt (requires a domain)
#         bash ssl-setup.sh --self-signed   → self-signed cert (IP / dev)
set -e

SERVER_IP="109.123.247.224"
DEPLOY_DIR="/opt/chartapnext-api"
NGINX_CONF="/etc/nginx/sites-available/chartapnext-api"

# ──────────────────────────────────────────────
# Parse arguments
# ──────────────────────────────────────────────
MODE=""
DOMAIN=""

if [[ "$1" == "--self-signed" ]]; then
    MODE="self-signed"
elif [[ -n "$1" ]]; then
    MODE="letsencrypt"
    DOMAIN="$1"
else
    echo ""
    echo "Usage:"
    echo "  bash ssl-setup.sh example.com          → Let's Encrypt (recommended)"
    echo "  bash ssl-setup.sh --self-signed         → self-signed cert (IP/dev)"
    echo ""
    read -rp "Enter domain name (or leave blank for self-signed): " DOMAIN
    if [[ -z "$DOMAIN" ]]; then
        MODE="self-signed"
    else
        MODE="letsencrypt"
    fi
fi

echo ""
echo "================================================"
echo " Chartapnext API — SSL Setup"
echo " Mode   : $MODE"
echo " Target : ${DOMAIN:-$SERVER_IP}"
echo "================================================"
echo ""

# ──────────────────────────────────────────────
# 1. Install required packages
# ──────────────────────────────────────────────
echo "[1/4] Installing packages..."
apt-get update -y -q
apt-get install -y -q nginx openssl

if [[ "$MODE" == "letsencrypt" ]]; then
    apt-get install -y -q certbot python3-certbot-nginx
fi

echo ">>> Packages ready"

# ──────────────────────────────────────────────
# 2. Obtain / generate certificate
# ──────────────────────────────────────────────
echo "[2/4] Obtaining SSL certificate..."

if [[ "$MODE" == "letsencrypt" ]]; then
    # --------------------------------------------------
    # Let's Encrypt via Certbot
    # --------------------------------------------------
    echo ">>> Running Certbot for $DOMAIN ..."
    certbot --nginx \
        -d "$DOMAIN" \
        --non-interactive \
        --agree-tos \
        --register-unsafely-without-email \
        --redirect

    CERT_PATH="/etc/letsencrypt/live/$DOMAIN/fullchain.pem"
    KEY_PATH="/etc/letsencrypt/live/$DOMAIN/privkey.pem"

    # Auto-renew cron (certbot installs a systemd timer, but add cron as backup)
    (crontab -l 2>/dev/null | grep -v certbot; \
     echo "0 3 * * * certbot renew --quiet && systemctl reload nginx") | crontab -
    echo ">>> Let's Encrypt certificate obtained. Auto-renew cron added."

else
    # --------------------------------------------------
    # Self-signed certificate (IP / dev / no domain)
    # --------------------------------------------------
    CERT_DIR="/etc/ssl/chartapnext"
    mkdir -p "$CERT_DIR"
    CERT_PATH="$CERT_DIR/cert.pem"
    KEY_PATH="$CERT_DIR/key.pem"

    echo ">>> Generating self-signed certificate for IP: $SERVER_IP ..."
    openssl req -x509 -nodes -newkey rsa:4096 -days 365 \
        -keyout "$KEY_PATH" \
        -out    "$CERT_PATH" \
        -subj   "/C=US/ST=State/L=City/O=Chartapnext/CN=$SERVER_IP" \
        -addext "subjectAltName=IP:$SERVER_IP"

    chmod 600 "$KEY_PATH"
    echo ">>> Self-signed certificate created (valid 365 days)"
    echo "    NOTE: Browsers will show a security warning — add an exception."
fi

# ──────────────────────────────────────────────
# 3. Write Nginx config with SSL
# ──────────────────────────────────────────────
echo "[3/4] Writing Nginx HTTPS config..."

SERVER_NAME="${DOMAIN:-$SERVER_IP}"

cat > "$NGINX_CONF" <<EOF
# HTTP → HTTPS redirect
server {
    listen 80;
    listen [::]:80;
    server_name ${SERVER_NAME};
    return 301 https://\$host\$request_uri;
}

# HTTPS
server {
    listen 443 ssl;
    listen [::]:443 ssl;
    server_name ${SERVER_NAME};

    ssl_certificate     ${CERT_PATH};
    ssl_certificate_key ${KEY_PATH};

    # Modern TLS only
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         HIGH:!aNULL:!MD5;
    ssl_prefer_server_ciphers on;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;
    add_header X-XSS-Protection "1; mode=block" always;
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;

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

ln -sf "$NGINX_CONF" /etc/nginx/sites-enabled/chartapnext-api
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl reload nginx
echo ">>> Nginx reloaded with SSL"

# ──────────────────────────────────────────────
# 4. Update .env ALLOWED_ORIGINS → https://
# ──────────────────────────────────────────────
echo "[4/4] Updating ALLOWED_ORIGINS in .env..."

HTTPS_ORIGIN="https://${SERVER_NAME}"

if grep -q "^ALLOWED_ORIGINS=" "$DEPLOY_DIR/.env"; then
    # Build new value: keep existing origins + add https version
    CURRENT=$(grep "^ALLOWED_ORIGINS=" "$DEPLOY_DIR/.env" | cut -d= -f2-)
    # Add https origin if not already present
    if echo "$CURRENT" | grep -q "$HTTPS_ORIGIN"; then
        echo ">>> ALLOWED_ORIGINS already contains $HTTPS_ORIGIN — no change"
    else
        NEW_ORIGINS="${HTTPS_ORIGIN},${CURRENT}"
        sed -i "s|^ALLOWED_ORIGINS=.*|ALLOWED_ORIGINS=${NEW_ORIGINS}|" "$DEPLOY_DIR/.env"
        echo ">>> ALLOWED_ORIGINS updated: $NEW_ORIGINS"
    fi
else
    echo "ALLOWED_ORIGINS=${HTTPS_ORIGIN},http://${SERVER_IP},http://localhost:3000" >> "$DEPLOY_DIR/.env"
    echo ">>> ALLOWED_ORIGINS added"
fi

# Restart API container to pick up new ALLOWED_ORIGINS
echo ">>> Restarting API container..."
cd "$DEPLOY_DIR" && docker compose up -d --build api

# ──────────────────────────────────────────────
# DONE
# ──────────────────────────────────────────────
echo ""
echo "================================================"
echo " SSL Setup Complete!"
echo "================================================"
echo ""
if [[ "$MODE" == "letsencrypt" ]]; then
    echo "  API (HTTPS) : https://${DOMAIN}/health"
    echo "  API Docs    : https://${DOMAIN}/docs"
    echo "  Auto-renew  : certbot renew (cron @ 3am daily)"
else
    echo "  API (HTTPS) : https://${SERVER_IP}/health"
    echo "  API Docs    : https://${SERVER_IP}/docs"
    echo "  NOTE        : Self-signed — accept browser security warning"
    echo "  Cert expires: in 365 days — re-run this script to renew"
fi
echo ""
