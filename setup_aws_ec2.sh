#!/usr/bin/env bash
# ==============================================================================
# Institutional Automated Deployment Script for Iron Bot on AWS EC2
# Target OS: Ubuntu 22.04 / 24.04 LTS (ap-south-1 Mumbai)
# Designed for Chirag Ravat - Zero Downtime, 24/7 Dedicated Server
# ==============================================================================

set -e

echo "🚀 [AWS SETUP] Starting Iron Bot Production Deployment on AWS EC2..."

# 1. Update system packages
echo "📦 Updating OS packages..."
sudo apt-get update -y
sudo apt-get install -y python3 python3-pip python3-venv git sqlite3 iptables-persistent

# 2. Setup directory
APP_DIR="/opt/iron_bot"
echo "📂 Setting up application directory at $APP_DIR..."
if [ ! -d "$APP_DIR" ]; then
    sudo git clone https://github.com/ravatchirag23/iron-bot.git "$APP_DIR"
else
    echo "Updating existing repository..."
    cd "$APP_DIR"
    sudo git pull origin main
fi

sudo chown -R ubuntu:ubuntu "$APP_DIR"
cd "$APP_DIR"

# 3. Virtual Environment & Dependencies
echo "🐍 Setting up Python Virtual Environment..."
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# 4. Port Forwarding (80 -> 5001 for clean GoCharting webhooks without port numbers)
echo "🌐 Configuring iptables port forwarding: 80 -> 5001..."
sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 5001
sudo sh -c "iptables-save > /etc/iptables/rules.v4" || true

# 5. Create Systemd Production Service (RHEL/L2 Enterprise Standard)
echo "⚙️ Creating systemd service: /etc/systemd/system/iron-bot.service..."
cat << 'EOF' | sudo tee /etc/systemd/system/iron-bot.service
[Unit]
Description=Iron Bot Autonomous Options Trading Engine
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/iron_bot
ExecStart=/opt/iron_bot/venv/bin/python3 /opt/iron_bot/webhook_server.py
Restart=always
RestartSec=3
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# 6. Enable and Start Daemon
echo "🔄 Reloading systemd daemons and enabling Iron Bot service..."
sudo systemctl daemon-reload
sudo systemctl enable iron-bot.service
sudo systemctl restart iron-bot.service

# 7. Verification & Health Check
sleep 3
if sudo systemctl is-active --quiet iron-bot.service; then
    PUBLIC_IP=$(curl -s http://checkip.amazonaws.com || curl -s http://ifconfig.me || echo "<YOUR_AWS_PUBLIC_IP>")
    echo ""
    echo "=========================================================================="
    echo "🎉 [SUCCESS] Iron Bot is LIVE & RUNNING 24/7 on AWS EC2!"
    echo "=========================================================================="
    echo "🔗 Webhook URL: http://$PUBLIC_IP/webhook"
    echo "🔗 Token Renewal: http://$PUBLIC_IP/update_token"
    echo "📜 Live Logs: sudo journalctl -u iron-bot.service -f"
    echo "=========================================================================="
else
    echo "❌ [ERROR] Service failed to start. Checking logs:"
    sudo journalctl -u iron-bot.service -n 20 --no-pager
    exit 1
fi
