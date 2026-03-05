#!/bin/bash
# VPS setup script for Polymarket arbitrage bot
# Run on a fresh Ubuntu 22.04+ VPS
# Usage: bash setup_vps.sh

set -e

echo "=== Polymarket Bot VPS Setup ==="

# System packages
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git ufw

# Firewall: only SSH + dashboard
sudo ufw allow 22/tcp
sudo ufw allow 8050/tcp
sudo ufw --force enable

# Create bot user
sudo useradd -m -s /bin/bash polybot 2>/dev/null || true

# Clone repo
sudo -u polybot bash -c '
cd /home/polybot
if [ ! -d polymarket-rn1 ]; then
    git clone https://github.com/mpprice/polymarket-rn1.git
fi
cd polymarket-rn1

# Python venv
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
pip install flask
'

echo ""
echo "=== Setup Complete ==="
echo ""
echo "Next steps:"
echo "  1. Copy your .env file:"
echo "     scp .env root@YOUR_VPS_IP:/home/polybot/polymarket-rn1/.env"
echo ""
echo "  2. Install systemd services:"
echo "     sudo cp /home/polybot/polymarket-rn1/deploy/polybot.service /etc/systemd/system/"
echo "     sudo cp /home/polybot/polymarket-rn1/deploy/polybot-dashboard.service /etc/systemd/system/"
echo "     sudo systemctl daemon-reload"
echo "     sudo systemctl enable --now polybot"
echo "     sudo systemctl enable --now polybot-dashboard"
echo ""
echo "  3. Check status:"
echo "     sudo systemctl status polybot"
echo "     sudo journalctl -u polybot -f"
echo ""
echo "  4. Dashboard will be at http://YOUR_VPS_IP:8050"
