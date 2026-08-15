#!/usr/bin/env bash
#
# Point the app at this Mac's current LAN IP.
#
# A phone cannot reach the Mac's 127.0.0.1 — that address resolves to the
# phone itself, so every request fails with a bare "Network error" and nothing
# in the UI explains why. The IP also changes whenever you switch networks or
# the DHCP lease turns over, which makes this a recurring five-minute bug
# rather than a one-time setup step.
#
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/mobile/.env"

detect_ip() {
  # macOS: ask each hardware port in turn; the first with an address wins.
  if command -v networksetup >/dev/null 2>&1; then
    for iface in $(networksetup -listallhardwareports 2>/dev/null | grep -oE 'en[0-9]+'); do
      ip="$(ipconfig getifaddr "$iface" 2>/dev/null || true)"
      [ -n "$ip" ] && { echo "$ip"; return; }
    done
  fi
  # Linux: whichever interface actually routes outbound (no packets sent).
  if command -v ip >/dev/null 2>&1; then
    ip="$(ip route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src") {print $(i+1); exit}}')"
    [ -n "$ip" ] && { echo "$ip"; return; }
  fi
  if command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    [ -n "$ip" ] && { echo "$ip"; return; }
  fi
  # Windows, under Git Bash.
  if command -v ipconfig.exe >/dev/null 2>&1 || [ -n "${WINDIR:-}" ]; then
    ip="$(ipconfig 2>/dev/null | tr -d '\r' | awk '/IPv4/ {print $NF}' | grep -v '^127\.' | head -1)"
    [ -n "$ip" ] && { echo "$ip"; return; }
  fi
}

IP="$(detect_ip || true)"

if [ -z "$IP" ]; then
  echo "error: could not find a LAN address — is Wi-Fi on?" >&2
  echo "       set it by hand in mobile/.env:" >&2
  echo "       EXPO_PUBLIC_API_URL=http://<your-lan-ip>:8000" >&2
  exit 1
fi

URL="http://${IP}:8000"
CURRENT="$(grep -E '^EXPO_PUBLIC_API_URL=' "$ENV_FILE" 2>/dev/null | cut -d= -f2- || true)"

cat > "$ENV_FILE" <<EOF
# A physical phone cannot reach the Mac's 127.0.0.1 — that resolves to the
# phone itself. This must be the Mac's LAN IP, and it goes stale whenever you
# change network. Re-run: mise run lan
# The iOS Simulator works with either, since it shares the Mac's network stack.
EXPO_PUBLIC_API_URL=${URL}
EOF

if [ "$CURRENT" = "$URL" ]; then
  echo "Already correct: ${URL}"
else
  echo "Updated mobile/.env"
  echo "  was: ${CURRENT:-<unset>}"
  echo "  now: ${URL}"

  # Expo inlines EXPO_PUBLIC_* at bundle time, so a Metro started before this
  # change keeps serving the old value however many times you reload the app.
  # Only worth saying when the value actually moved. curl rather than lsof,
  # which isn't reliably present outside macOS.
  if curl -s -o /dev/null --max-time 3 http://localhost:8081/ 2>/dev/null; then
    echo
    echo "Metro is running with the OLD value baked in — restart it: mise run mobile"
  fi
fi

echo
if curl -s -o /dev/null --max-time 5 "${URL}/api/library/" 2>/dev/null; then
  echo "Backend reachable at ${URL} ✓"
else
  echo "Backend not answering at ${URL} yet — start it with: mise run backend"
fi
