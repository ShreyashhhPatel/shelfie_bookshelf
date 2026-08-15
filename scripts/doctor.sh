#!/usr/bin/env bash
#
# Check everything the app needs, and say what to run when something is off.
# Never exits non-zero on a warning — this is a diagnostic, not a gate.
#
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND="$ROOT/backend"
MOBILE="$ROOT/mobile"
# Windows virtualenvs put the interpreter in Scripts/, not bin/.
PY="$BACKEND/venv/bin/python"
[ -x "$PY" ] || PY="$BACKEND/venv/Scripts/python.exe"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$1"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$1"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$1"; }

echo
echo "Toolchain"
command -v node >/dev/null && ok "node        $(node --version)" || bad "node missing"
command -v npm  >/dev/null && ok "npm         $(npm --version)"  || bad "npm missing"
if [ -x "$PY" ]; then ok "python      $($PY --version 2>&1 | cut -d' ' -f2) (backend/venv)"
else bad "backend/venv missing            -> mise run setup"; fi

echo
echo "Backend"
if [ -x "$PY" ]; then
  $PY -c "import django, ultralytics, cv2, PIL, rapidfuzz" 2>/dev/null \
    && ok "python deps installed" \
    || bad "python deps incomplete         -> mise run setup"

  if [ -f "$BACKEND/.env" ] && grep -q "GEMINI_API_KEY=." "$BACKEND/.env" 2>/dev/null; then
    ok "GEMINI_API_KEY present"
  else
    warn "no GEMINI_API_KEY in backend/.env  (tests still run; real scans won't)"
  fi

  if [ -f "$BACKEND/db.sqlite3" ]; then
    PENDING=$(cd "$BACKEND" && $PY manage.py showmigrations --plan 2>/dev/null | grep -c '^\[ \]')
    [ "${PENDING:-0}" -eq 0 ] && ok "migrations up to date" || warn "$PENDING unapplied migration(s) -> mise run setup"
    COUNT=$(cd "$BACKEND" && $PY -c "
import os,django; os.environ.setdefault('DJANGO_SETTINGS_MODULE','shelfie.settings'); django.setup()
from scanner.models import CatalogBook; print(CatalogBook.objects.count())" 2>/dev/null)
    [ "${COUNT:-0}" -gt 0 ] && ok "catalog loaded ($COUNT books)" || warn "catalog empty -> mise run catalog"
  else
    warn "no database yet                   -> mise run setup"
  fi

  MODEL=$(cd "$BACKEND" && $PY -c "from scanner.constants import YOLO_MODEL_NAME; print(YOLO_MODEL_NAME)" 2>/dev/null)
  if [ -f "$BACKEND/${MODEL:-none}" ]; then ok "YOLO weights present ($MODEL)"
  else warn "YOLO weights ${MODEL:-?} not downloaded yet (auto-downloads on first scan)"; fi
fi

echo
echo "Mobile"
if [ -d "$MOBILE/node_modules" ]; then
  ok "node_modules installed"
  ok "Expo SDK    $(node -p "require('$MOBILE/package.json').dependencies.expo" 2>/dev/null)"
  ok "React Native $(node -p "require('$MOBILE/package.json').dependencies['react-native']" 2>/dev/null)"
else
  bad "node_modules missing              -> mise run setup"
fi

API=$(grep -E '^EXPO_PUBLIC_API_URL=' "$MOBILE/.env" 2>/dev/null | cut -d= -f2-)
if [ -z "$API" ]; then
  warn "EXPO_PUBLIC_API_URL unset         -> mise run lan"
elif echo "$API" | grep -q '127.0.0.1\|localhost'; then
  warn "API URL is $API (simulator/web only; a phone can't reach it) -> mise run lan"
else
  ok "API URL     $API"
fi

echo
echo "Running services"
curl -s -o /dev/null --max-time 3 http://127.0.0.1:8000/api/library/ 2>/dev/null \
  && ok "backend up on :8000" || warn "backend not running               -> mise run backend"
curl -s -o /dev/null --max-time 3 http://localhost:8081/ 2>/dev/null \
  && ok "Metro up on :8081"   || warn "Metro not running                 -> mise run mobile"
echo
