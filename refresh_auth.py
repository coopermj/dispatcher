#!/usr/bin/env python3
"""Interactively refresh the pipeline's tokens — run on the Mac, not the box.

1. Google token (token.pickle): refreshes silently; opens a browser only if
   re-consent is required (revoked token or scope change).
2. Dispatch cookies (dispatch_cookies.json): tests the saved cookies; if
   they're dead, opens a headed browser for the magic-link login and saves
   fresh ones (the existing AuthManager flow).
3. Atlantic cookies (atlantic_cookies.json): same idea for The Atlantic, so
   linked theatlantic.com pages render unpaywalled. Optional — a skipped or
   failed Atlantic login is reported but does not fail this script.

refresh_tokens_mac.sh runs this and then copies everything to the box.
"""
import asyncio
import sys

from modules.auth import AuthManager
from modules.browser_manager import BrowserManager


async def refresh_dispatch(auth_manager):
    bm = BrowserManager()
    if not await bm.start_browser_session():
        print("❌ Browser failed to start")
        return False
    try:
        ok = await auth_manager.authenticate_with_dispatch(
            bm.get_page(), bm.get_context(), interactive=True)
        print("— Atlantic cookies —")
        if not await auth_manager.authenticate_with_atlantic(
                bm.get_page(), bm.get_context(), interactive=True):
            print("⚠️ Atlantic login not completed — linked Atlantic pages will render truncated")
        return ok
    finally:
        await bm.close_browser_session()


def main():
    am = AuthManager()
    print("— Google token —")
    if not am.authenticate_google():
        print("❌ Google auth failed")
        return 1
    print("— Dispatch cookies —")
    if not asyncio.run(refresh_dispatch(am)):
        print("❌ Dispatch auth failed")
        return 1
    print("✅ Local tokens fresh")
    return 0


if __name__ == "__main__":
    sys.exit(main())
