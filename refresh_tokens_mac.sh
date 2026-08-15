#!/bin/zsh
# Refresh all pipeline tokens on this Mac and deploy them to the headless box.
#
# Run this when the box emails you an auth failure (or preemptively). It:
#   1. refreshes the Google token (browser opens only if re-consent needed)
#   2. tests/refreshes Dispatch cookies (headed browser for magic-link login
#      only if the saved cookies are dead)
#   3. refreshes the rmapi user token (interactive re-register only if the
#      device token itself was revoked)
#   4. copies token.pickle, dispatch_cookies.json, and rmapi.conf to the box
#   5. verifies everything works FROM the box
set -euo pipefail
cd "$(dirname "$0")"
REMOTE=micah@dispatcher.tail48ca7.ts.net

echo "==> 1/4 Google token + Dispatch cookies (a browser may open)"
BROWSER_HEADLESS=false .venv/bin/python refresh_auth.py

echo "==> 2/4 rmapi token"
if ~/rmapi/rmapi ls >/dev/null 2>&1; then
    echo "    rmapi OK (user token refreshed)"
else
    echo "    rmapi auth invalid — get a one-time code from:"
    echo "    https://my.remarkable.com/device/desktop/connect"
    ~/rmapi/rmapi ls   # interactive registration prompt
fi

echo "==> 3/4 Copying tokens to $REMOTE"
scp -q token.pickle dispatch_cookies.json "$REMOTE:~/dispatchweb/"
scp -q "$HOME/Library/Application Support/rmapi/rmapi.conf" "$REMOTE:~/.config/rmapi/rmapi.conf"

echo "==> 4/4 Verifying from the box"
ssh "$REMOTE" '~/rmapi/rmapi ls >/dev/null 2>&1 && echo "    rmapi: OK" || echo "    rmapi: FAILED"
cd ~/dispatchweb && .venv/bin/python - <<PYEOF
import json, pickle
c = pickle.load(open("token.pickle", "rb"))
scopes = " ".join(c.scopes or [])
print("    google token: refresh_token =", bool(c.refresh_token),
      "| gmail.send =", "gmail.send" in scopes)
print("    dispatch cookies:", len(json.load(open("dispatch_cookies.json"))))
PYEOF'

echo "✅ Tokens refreshed and deployed to the box."
