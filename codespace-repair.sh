#!/usr/bin/env bash
set -euo pipefail

cd /workspaces/AA-Efficiency-Dashboard 2>/dev/null || cd "$(dirname "$0")"

echo "Updating AA Efficiency Dashboard..."
git pull --ff-only

echo "Installing Python dependencies..."
python -m pip install -r requirements.txt

echo "Installing Playwright Linux system dependencies..."
sudo -n python -m playwright install-deps chromium

echo "Ensuring Playwright Chromium exists..."
python -m playwright install chromium

echo "Restarting dashboard..."
pkill -f '[p]ython.*app.py' 2>/dev/null || true
nohup python app.py >/tmp/aa-dashboard.log 2>&1 &

sleep 2

echo
echo "AA Efficiency Dashboard restarted."
echo "Open the forwarded port 8765 from the Ports tab."
echo
