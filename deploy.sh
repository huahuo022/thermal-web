#!/usr/bin/env bash
# Pull the latest code and redeploy the systemd service.
#
#   cd /root/thermal-web && sudo ./deploy.sh
#
set -euo pipefail

cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"
DEST="${1:-/opt/thermal-web}"

# Explicit fetch + merge instead of `git pull`: pull falls back to fetching
# every branch when the upstream is not resolvable, and then dies with
# "Cannot fast-forward to multiple branches".
BRANCH="$(git rev-parse --abbrev-ref HEAD)"
echo "==> updating branch $BRANCH from origin"
git fetch --prune origin "$BRANCH"
git merge --ff-only "origin/$BRANCH"

echo "==> deploying to $DEST"
exec ./install.sh "$DEST"
