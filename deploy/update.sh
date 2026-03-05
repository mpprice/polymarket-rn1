#!/bin/bash
# Pull latest code and restart services
# Usage: bash deploy/update.sh

set -e

cd /home/polybot/polymarket-rn1
git pull origin main
source venv/bin/activate
pip install -r requirements.txt --quiet

sudo systemctl restart polybot
sudo systemctl restart polybot-dashboard

echo "Updated and restarted. Status:"
sudo systemctl status polybot --no-pager -l | head -10
sudo systemctl status polybot-dashboard --no-pager -l | head -5
