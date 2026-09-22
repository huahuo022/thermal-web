#!/usr/bin/env bash
# Install thermal-web as a systemd service (Debian/Raspbian/Armbian friendly).
set -euo pipefail

DEST="${1:-/opt/thermal-web}"
PORT="${PORT:-8080}"
SOURCE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

[ "$(id -u)" = "0" ] || { echo "please run as root"; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }

echo "==> installing to $DEST"
mkdir -p "$DEST"
for item in server.py escpos.py transport.py store.py web; do
  cp -r "$SOURCE/$item" "$DEST/"
done
mkdir -p "$DEST/data"
chmod -R a+rX "$DEST"

if [ ! -f /etc/thermal-web.env ]; then
  cat > /etc/thermal-web.env <<'ENVEOF'
# thermal-web environment - uncomment and edit to enable HTTP basic auth.
#THERMAL_WEB_USER=admin
#THERMAL_WEB_PASSWORD=change-me
ENVEOF
  chmod 600 /etc/thermal-web.env
  echo "==> created /etc/thermal-web.env (600) - edit it to set a password"
fi

echo "==> writing systemd unit"
sed -e "s#/opt/thermal-web#$DEST#g" -e "s#--port 8080#--port $PORT#" \
  "$SOURCE/systemd/thermal-web.service" > /etc/systemd/system/thermal-web.service
systemctl daemon-reload
systemctl enable thermal-web
systemctl restart thermal-web

echo
systemctl --no-pager --lines=0 status thermal-web || true
echo
echo "==> done.  open http://<host>:$PORT"
echo "    hint: to enable auth, add Environment=THERMAL_WEB_PASSWORD=... to the unit"
