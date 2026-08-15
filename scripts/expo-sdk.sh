#!/usr/bin/env bash
#
# Switch the Expo SDK the mobile app targets.
#
# Expo Go on the App Store only ever supports one SDK — the current one. If a
# phone has an older Expo Go, the project has to come down to meet it, because
# the phone cannot come up. That is the entire reason this script exists.
#
#   scripts/expo-sdk.sh          # show what's installed and what's available
#   scripts/expo-sdk.sh 54       # switch the project to SDK 54
#
set -euo pipefail

MOBILE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../mobile" && pwd)"
cd "$MOBILE_DIR"

current() { node -p "require('./package.json').dependencies.expo" 2>/dev/null || echo "not installed"; }

if [ $# -eq 0 ] || [ -z "${1:-}" ]; then
  echo "Expo SDK currently targeted: $(current)"
  echo
  echo "Published SDKs (npm dist-tags for 'expo'):"
  npm view expo dist-tags --json 2>/dev/null \
    | grep -oE '"sdk-[0-9]+": "[^"]+"' \
    | sed 's/"sdk-/  SDK /; s/": "/  -> /; s/"$//' \
    | sort -V
  echo "  latest    -> $(npm view expo dist-tags.latest 2>/dev/null)"
  echo
  echo "Switch with:  mise run sdk <version>     e.g. mise run sdk 54"
  echo
  echo "Pick the SDK your Expo Go app supports. Expo Go shows it on its home"
  echo "screen; a project on a newer SDK simply refuses to load."
  exit 0
fi

VERSION="${1%%.*}"   # accept "54" or "54.0.0"

if ! [[ "$VERSION" =~ ^[0-9]+$ ]]; then
  echo "error: '$1' is not an SDK version. Expected a number like 54." >&2
  exit 1
fi

# Fail before touching node_modules if the SDK was never published. Without
# this, npm installs nothing and the project is left looking switched.
# `|| true` matters: npm exits non-zero on a 404, and under `set -e` that
# would kill the script here, before the friendly message below ever prints.
# When the range matches several releases npm prints "expo@54.0.36 '54.0.36'"
# per line, so take the last line's last field and unquote it.
RESOLVED="$(npm view "expo@~${VERSION}.0.0" version 2>/dev/null | tail -1 | awk '{print $NF}' | tr -d "'\"" || true)"
if [ -z "$RESOLVED" ]; then
  echo "error: no published expo release matching ~${VERSION}.0.0" >&2
  echo "       run 'mise run sdk' with no arguments to list the real ones." >&2
  exit 1
fi

echo "Switching Expo SDK: $(current)  ->  ~${VERSION}.0.0  (resolves to ${RESOLVED})"
echo

# Order matters. Pinning expo first tells `expo install --fix` which SDK to
# align everything else to; running --fix first would align to the old one.
npm install "expo@~${VERSION}.0.0"
echo
echo "Aligning every other dependency to SDK ${VERSION}..."
npx expo install --fix

echo
echo "Now targeting:"
node -p "
const p = require('./package.json');
const keep = k => k === 'expo' || k.startsWith('expo-') || k.startsWith('react') || k === '@expo/metro-runtime';
Object.entries({...p.dependencies, ...p.devDependencies})
  .filter(([k]) => keep(k))
  .map(([k, v]) => '  ' + k.padEnd(34) + v)
  .join('\n')
"
echo
echo "Restart Metro so the new packages are bundled:  mise run mobile"
