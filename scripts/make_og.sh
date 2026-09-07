#!/bin/sh
# Render scripts/og-card.html to assets/og-image.png at 1200x630 (the size
# Facebook, X, WhatsApp and LinkedIn all crop from). Uses headless Chrome so
# there is no image library to install.
set -e
cd "$(dirname "$0")/.."
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
[ -x "$CHROME" ] || CHROME="$(command -v chromium || command -v google-chrome)"
[ -x "$CHROME" ] || { echo "Chrome not found — install it or export CHROME=..." >&2; exit 1; }
"$CHROME" --headless --disable-gpu --hide-scrollbars \
  --force-device-scale-factor=1 --window-size=1200,630 \
  --virtual-time-budget=8000 \
  --screenshot="assets/og-image.png" \
  "file://$PWD/scripts/og-card.html" >/dev/null 2>&1
echo "wrote assets/og-image.png ($(du -h assets/og-image.png | cut -f1))"
