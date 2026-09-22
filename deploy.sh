#!/usr/bin/env bash
# Pull the latest code and redeploy the systemd service.
#
#   cd /root/thermal-web && sudo ./deploy.sh
#
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
DEST="${1:-/opt/thermal-web}"

echo "==> git pull --ff-only"
git pull --ff-only

echo "==> deploying to $DEST"
exec ./install.sh "$DEST"
