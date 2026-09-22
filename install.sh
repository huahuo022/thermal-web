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
# copy every module (not a hardcoded list, so new files cannot be forgotten)
cp -f "$SOURCE"/*.py "$DEST/"
cp -rf "$SOURCE/web" "$DEST/"
mkdir -p "$DEST/data"
chmod -R a+rX "$DEST"

if [ ! -f /etc/thermal-web.env ]; then
  cat > /etc/thermal-web.env <<'ENVEOF'
# thermal-web environment - uncomment and edit to require a login.
#THERMAL_WEB_USER=admin
#THERMAL_WEB_PASSWORD=change-me
# Store a hash instead of the plaintext password (takes precedence):
#THERMAL_WEB_PASSWORD_SHA256=
# Session lifetime in hours (default 168 = 7 days), and set to 1 when
# serving the UI over HTTPS so the cookie is only sent over TLS:
#THERMAL_WEB_SESSION_HOURS=168
#THERMAL_WEB_COOKIE_SECURE=0
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
echo "    hint: to enable auth, put THERMAL_WEB_PASSWORD in /etc/thermal-web.env"
echo "          (chmod 600) and run: systemctl restart thermal-web"
