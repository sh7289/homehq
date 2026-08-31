#!/usr/bin/env bash
set -euo pipefail

cd /home/homehq/homehq

# Pull latest from GitHub (safe even if already up to date)
git pull

# Install/update dependencies in the venv
source .venv/bin/activate
pip install -r requirements.txt -q

# Restart service so code changes take effect
sudo systemctl restart homehq

echo "✅ Home HQ deployed"
