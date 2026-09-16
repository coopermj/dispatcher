#!/usr/bin/env python3
"""
Authentication manager for Google OAuth and The Dispatch
"""

import os
import pickle
import json
import re
import sys
import time
import asyncio
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config.settings import (
    GOOGLE_SCOPES, CREDENTIALS_FILE, TOKEN_FILE, COOKIES_FILE,
    DISPATCH_BASE_URL, BROWSER_HEADLESS,
    ATLANTIC_COOKIES_FILE, ATLANTIC_BASE_URL, ATLANTIC_LOGIN_URL
)
from modules.alerts import record_failure

# Reads the page's own login state. The Dispatch gates its account UI with
# Alpine `x-show="$store.user.loaded && $store.user.valid"`; the markup itself
# (Log Out / My Account) is in the DOM for every visitor, so only the store
# tells the truth. Polls briefly because the store is populated asynchronously
# (Piano) after load. Returns {loaded, valid}; never throws into Python.
LOGIN_STATE_JS = """
async () => {
  const deadline = Date.now() + 8000;
  while (Date.now() < deadline) {
    try {
      const s = window.Alpine && Alpine.store && Alpine.store('user');
      if (s && s.loaded) return {loaded: true, valid: !!s.valid};
    } catch (e) {}
    await new Promise(r => setTimeout(r, 250));
  }
  return {loaded: false, valid: false};
}
"""


async def is_logged_in(page):
    """True only when the page reports a loaded AND valid member session.

    Anything else — store never loaded, Alpine missing, evaluate failed — is
    False, so an undetectable state fails closed and gets alerted rather than
    proceeding as if signed in (the failure mode this replaced).
    """
    try:
        state = await page.evaluate(LOGIN_STATE_JS)
    except Exception as e:
        print(f"⚠️ Could not read login state: {e}")
        return False
    if not isinstance(state, dict):
        return False
    return bool(state.get('loaded')) and bool(state.get('valid'))


# The Atlantic renders login state server-side (probe 2026-09-16): every page
# embeds "isLoggedIn": true|false, and the nav shows My Account
# (accounts.theatlantic.com/accounts/details/) when signed in vs Sign In
# (accounts.theatlantic.com/login/) when not. So, unlike The Dispatch, a static
# HTML check is trustworthy here.
_ATLANTIC_FLAG_RE = re.compile(r'"isLoggedIn"\s*:\s*(true|false)')
ATLANTIC_ACCOUNT_LINK = 'accounts.theatlantic.com/accounts/details/'


def atlantic_looks_logged_in(html):
    """True if the page says we're a signed-in Atlantic member. An explicit
    isLoggedIn:false wins over everything; with no flag, the My Account link counts."""
    if not html:
        return False
    flags = set(_ATLANTIC_FLAG_RE.findall(html))
    if 'false' in flags:
        return False
    if 'true' in flags:
        return True
    return ATLANTIC_ACCOUNT_LINK in html


async def check_atlantic_session(auth_manager, page, browser_context, failures):
    """Once-per-run, NON-fatal Atlantic session check for pipelines that follow links.

    No jar → the feature was never set up: print a hint, record nothing (no
    nightly noise). Jar present but dead → record an 'auth' failure so the
    end-of-run alert points at refresh_tokens_mac.sh, but let the run continue:
    linked pages are secondary content; only Dispatch auth aborts a run.
    """
    if not Path(ATLANTIC_COOKIES_FILE).exists():
        print("ℹ️ No Atlantic cookie jar — linked theatlantic.com pages will render as a "
              "non-subscriber (run ./refresh_tokens_mac.sh to add one)")
        return False
    if await auth_manager.authenticate_with_atlantic(page, browser_context, interactive=False):
        return True
    record_failure(failures, "auth", "The Atlantic",
                   "session expired — linked Atlantic pages will render truncated")
    return False


class AuthManager:
    """Handles Google OAuth and The Dispatch authentication"""

    def __init__(self):
        self.creds = None
        self.service = None
        self.user_info = None
        self.authenticated_with_dispatch = False

    def authenticate_google(self):
        """Authenticate with Google (for both Gmail and The Dispatch)"""
        print("🔐 Authenticating with Google...")
        creds = None

        if TOKEN_FILE.exists():
            with open(TOKEN_FILE, 'rb') as token:
                creds = pickle.load(token)

        # A token from before a scope addition (e.g. gmail.send for failure
        # alerts) stays "valid" but can't use the new scope — force re-consent.
        if creds and not set(GOOGLE_SCOPES).issubset(set(creds.scopes or [])):
            print("🔐 Stored Google token is missing newly required scopes — re-consent needed")
            creds = None

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"🔧 Token refresh failed: {e}")
                    creds = None

            if not creds:
                if not CREDENTIALS_FILE.exists():
                    print(f"❌ Please download Google OAuth credentials as '{CREDENTIALS_FILE}'")
                    return False

                flow = InstalledAppFlow.from_client_secrets_file(
                    CREDENTIALS_FILE, GOOGLE_SCOPES)
                creds = flow.run_local_server(port=8080)

            with open(TOKEN_FILE, 'wb') as token:
                pickle.dump(creds, token)

        self.creds = creds
        self.service = build('gmail', 'v1', credentials=creds)

        # Get user info for The Dispatch login
        try:
            oauth_service = build('oauth2', 'v2', credentials=creds)
            self.user_info = oauth_service.userinfo().get().execute()
            print(f"✅ Authenticated as: {self.user_info.get('email')}")
        except Exception as e:
            print(f"⚠️ Could not get user info: {e}")
            self.user_info = {'email': 'unknown@gmail.com'}

        print("✅ Google authentication complete")
        return True

    # ---- cookie jars (one per site; loaded into the shared browser context) ----

    async def _save_cookies(self, browser_context, domain, path, label):
        """Persist the context's cookies for `domain` to `path`."""
        try:
            if not browser_context:
                print(f"⚠️ No browser context available for saving {label} cookies")
                return False
            cookies = [c for c in await browser_context.cookies()
                       if domain in c.get('domain', '')]
            if not cookies:
                print(f"⚠️ No {label} cookies found to save")
                return False
            with open(path, 'w') as f:
                json.dump(cookies, f, indent=2)
            print(f"✅ Saved {len(cookies)} {label} cookies to {path}")
            return True
        except Exception as e:
            print(f"❌ Error saving {label} cookies: {e}")
            return False

    async def _load_cookies(self, browser_context, path, label):
        """Add the saved cookies at `path` to the context, dropping any already
        expired (e.g. Cloudflare's ~30-minute __cf_bm) so add_cookies never chokes."""
        try:
            if not Path(path).exists():
                print(f"📝 No saved {label} cookies found at {path}")
                return False
            if not browser_context:
                print(f"⚠️ No browser context available for loading {label} cookies")
                return False
            with open(path, 'r') as f:
                cookies = json.load(f)
            now = time.time()
            live = [c for c in cookies
                    if not c.get('expires') or c['expires'] < 0 or c['expires'] > now]
            if not live:
                print(f"⚠️ All saved {label} cookies have expired")
                return False
            await browser_context.add_cookies(live)
            print(f"✅ Loaded {len(live)} {label} cookies from {path}")
            return True
        except Exception as e:
            print(f"❌ Error loading {label} cookies: {e}")
            return False

    async def save_dispatch_cookies(self, browser_context):
        """Save The Dispatch browser cookies to file"""
        return await self._save_cookies(browser_context, 'thedispatch.com', COOKIES_FILE, 'Dispatch')

    async def load_dispatch_cookies(self, browser_context):
        """Load The Dispatch browser cookies from file"""
        return await self._load_cookies(browser_context, COOKIES_FILE, 'Dispatch')

    async def save_atlantic_cookies(self, browser_context):
        return await self._save_cookies(browser_context, 'theatlantic.com', ATLANTIC_COOKIES_FILE, 'Atlantic')

    async def load_atlantic_cookies(self, browser_context):
        return await self._load_cookies(browser_context, ATLANTIC_COOKIES_FILE, 'Atlantic')

    # ---- The Atlantic ----

    async def test_atlantic_authentication(self, page):
        """Load the Atlantic homepage and read its server-rendered login state."""
        try:
            print("🔍 Testing The Atlantic session...")
            await page.goto(ATLANTIC_BASE_URL, timeout=30000)
            await asyncio.sleep(3)
            if atlantic_looks_logged_in(await page.content()):
                print("✅ The Atlantic: signed in with saved cookies")
                return True
            print("❌ The Atlantic: not signed in (page shows Sign In / isLoggedIn=false)")
            return False
        except Exception as e:
            print(f"⚠️ Error testing The Atlantic session: {e}")
            return False

    async def authenticate_with_atlantic(self, page, browser_context, interactive=None):
        """Sign in to The Atlantic via saved cookies, or interactively on a Mac.

        Mirrors authenticate_with_dispatch: fails closed, never overwrites the
        jar on failure. Interactive login goes through the site's own form
        (which may show a CAPTCHA — the human solves it); we only keep cookies.
        """
        if interactive is None:
            interactive = sys.stdin.isatty() and not BROWSER_HEADLESS
        try:
            if await self.load_atlantic_cookies(browser_context):
                if await self.test_atlantic_authentication(page):
                    return True
                print("🔄 Saved Atlantic cookies expired or invalid")

            if not interactive:
                print("❌ Not signed in to The Atlantic and no interactive session to sign in with")
                return False

            print("🔑 The Atlantic: sign in in the browser window (up to 5 minutes)...")
            await page.goto(ATLANTIC_LOGIN_URL, timeout=30000)
            max_wait_seconds, check_interval, elapsed = 300, 3, 0
            while elapsed < max_wait_seconds:
                await asyncio.sleep(check_interval)
                elapsed += check_interval
                try:
                    if atlantic_looks_logged_in(await page.content()):
                        print("✅ The Atlantic login detected!")
                        await self.save_atlantic_cookies(browser_context)
                        return True
                except Exception:
                    pass  # mid-navigation; try again next tick
                if elapsed % 30 == 0:
                    print(f"⏳ Still waiting for The Atlantic login... ({elapsed}s)")
            print("⏰ Timed out waiting for The Atlantic login — nothing saved")
            return False
        except Exception as e:
            print(f"❌ The Atlantic authentication error: {e}")
            return False

    # ---- The Dispatch ----

    async def test_dispatch_authentication(self, page):
        """Test if we're already authenticated with The Dispatch"""
        try:
            print("🔍 Testing existing authentication...")

            # Try to access the homepage first and check for login indicators
            await page.goto(DISPATCH_BASE_URL, timeout=30000)
            await asyncio.sleep(3)

            if await is_logged_in(page):
                print("✅ Already authenticated with saved cookies!")
                self.authenticated_with_dispatch = True
                return True

            print("❌ Not authenticated - the site reports no valid member session")
            return False

        except Exception as e:
            print(f"⚠️ Error testing authentication: {e}")
            return False

    async def _check_logged_in_quietly(self, page):
        """Quietly check if logged in without navigation or verbose output"""
        return await is_logged_in(page)

    async def authenticate_with_dispatch(self, page, browser_context, interactive=None):
        """Authenticate with The Dispatch using saved cookies or manual login.

        Returns False (never a hopeful True) when we cannot confirm a login, so the
        caller can alert. `interactive=None` auto-detects: a manual login is only
        possible with a terminal to read and a visible browser window to type into.
        """
        if self.authenticated_with_dispatch:
            return True
        if interactive is None:
            interactive = sys.stdin.isatty() and not BROWSER_HEADLESS

        try:
            print("🔐 Authenticating with The Dispatch...")

            # First, try to load existing cookies
            cookies_loaded = await self.load_dispatch_cookies(browser_context)

            if cookies_loaded:
                # Test if the loaded cookies work
                if await self.test_dispatch_authentication(page):
                    return True
                else:
                    print("🔄 Saved cookies expired or invalid, need fresh authentication")

            if not interactive:
                # Cron / headless: nobody can complete a magic-link login here. Fail
                # closed and keep the existing cookie file for the interactive refresh.
                print("❌ Not logged in to The Dispatch and no interactive session to log in with")
                print("💡 Refresh cookies: run ./refresh_tokens_mac.sh (or main.py in a terminal)")
                return False

            # If we get here, we need to authenticate manually
            print("🔑 Manual authentication required")
            print("=" * 50)
            print("INSTRUCTIONS:")
            print("1. A browser window is now open")
            print("2. Navigate to The Dispatch and log in using your magic link")
            print("3. Complete the authentication process")
            print("4. Login will be detected automatically")
            print("5. Your session will be saved for future runs")
            print("=" * 50)

            # Open The Dispatch homepage
            await page.goto(DISPATCH_BASE_URL, timeout=30000)
            await asyncio.sleep(2)

            # Poll for login completion instead of waiting for ENTER
            print("\n⏳ Waiting for login (checking every 3 seconds)...")
            max_wait_seconds = 300  # 5 minutes timeout
            check_interval = 3
            elapsed = 0

            while elapsed < max_wait_seconds:
                await asyncio.sleep(check_interval)
                elapsed += check_interval

                # Check if logged in
                if await self._check_logged_in_quietly(page):
                    print("\n✅ Login detected!")
                    await self.save_dispatch_cookies(browser_context)
                    print("✅ Authentication successful and cookies saved!")
                    self.authenticated_with_dispatch = True
                    return True

                # Show progress every 15 seconds
                if elapsed % 15 == 0:
                    print(f"⏳ Still waiting for login... ({elapsed}s elapsed)")

            # Timeout reached
            print(f"\n⏰ Timeout after {max_wait_seconds}s - checking final state...")
            if await self.test_dispatch_authentication(page):
                await self.save_dispatch_cookies(browser_context)
                print("✅ Authentication successful and cookies saved!")
                self.authenticated_with_dispatch = True
                return True
            else:
                print("❌ Authentication verification failed — existing cookie file left untouched")
                return False

        except Exception as e:
            print(f"❌ Authentication error: {e}")
            return False

    def get_gmail_service(self):
        """Get authenticated Gmail service"""
        if not self.service:
            self.authenticate_google()
        return self.service

    def get_user_email(self):
        """Get authenticated user's email"""
        if not self.user_info:
            return "unknown@gmail.com"
        return self.user_info.get('email', 'unknown@gmail.com')

    def is_authenticated(self):
        """Check if authenticated with both Google and The Dispatch"""
        return bool(self.creds and self.authenticated_with_dispatch)