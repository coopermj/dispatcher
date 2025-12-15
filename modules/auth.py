#!/usr/bin/env python3
"""
Authentication manager for Google OAuth and The Dispatch
"""

import os
import pickle
import json
import asyncio
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from config.settings import (
    GOOGLE_SCOPES, CREDENTIALS_FILE, TOKEN_FILE, COOKIES_FILE,
    DISPATCH_BASE_URL, LOGIN_INDICATORS, LOGGED_IN_INDICATORS
)


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

    async def save_dispatch_cookies(self, browser_context):
        """Save The Dispatch browser cookies to file"""
        try:
            if not browser_context:
                print("⚠️ No browser context available for saving cookies")
                return False

            cookies = await browser_context.cookies()

            # Filter for The Dispatch cookies
            dispatch_cookies = [
                cookie for cookie in cookies
                if 'thedispatch.com' in cookie.get('domain', '')
            ]

            if dispatch_cookies:
                with open(COOKIES_FILE, 'w') as f:
                    json.dump(dispatch_cookies, f, indent=2)
                print(f"✅ Saved {len(dispatch_cookies)} cookies to {COOKIES_FILE}")
                return True
            else:
                print("⚠️ No Dispatch cookies found to save")
                return False

        except Exception as e:
            print(f"❌ Error saving cookies: {e}")
            return False

    async def load_dispatch_cookies(self, browser_context):
        """Load The Dispatch browser cookies from file"""
        try:
            if not COOKIES_FILE.exists():
                print(f"📝 No saved cookies found at {COOKIES_FILE}")
                return False

            if not browser_context:
                print("⚠️ No browser context available for loading cookies")
                return False

            with open(COOKIES_FILE, 'r') as f:
                cookies = json.load(f)

            if cookies:
                await browser_context.add_cookies(cookies)
                print(f"✅ Loaded {len(cookies)} cookies from {COOKIES_FILE}")
                return True
            else:
                print("⚠️ No cookies found in file")
                return False

        except Exception as e:
            print(f"❌ Error loading cookies: {e}")
            return False

    async def test_dispatch_authentication(self, page):
        """Test if we're already authenticated with The Dispatch"""
        try:
            print("🔍 Testing existing authentication...")

            # Try to access the homepage first and check for login indicators
            await page.goto(DISPATCH_BASE_URL, timeout=30000)
            await asyncio.sleep(3)

            page_content = await page.content()

            # Look for indicators that we're logged in vs need to log in
            has_login_indicators = any(indicator in page_content.lower() for indicator in LOGIN_INDICATORS)
            has_logged_in_indicators = any(indicator in page_content.lower() for indicator in LOGGED_IN_INDICATORS)

            # If we have logout/account indicators and no login prompts, we're likely logged in
            if has_logged_in_indicators and not has_login_indicators:
                print("✅ Already authenticated with saved cookies!")
                self.authenticated_with_dispatch = True
                return True
            else:
                print("❌ Not authenticated - need to log in")
                return False

        except Exception as e:
            print(f"⚠️ Error testing authentication: {e}")
            return False

    async def _check_logged_in_quietly(self, page):
        """Quietly check if logged in without navigation or verbose output"""
        try:
            # Check for logged-in specific elements using JavaScript
            # This is more reliable than text matching since menus always show "sign in"
            is_logged_in = await page.evaluate("""
                () => {
                    // Check for user avatar/profile elements (common in logged-in state)
                    const avatarSelectors = [
                        '[data-testid="user-avatar"]',
                        '[data-testid="account-menu"]',
                        '.user-avatar',
                        '.avatar',
                        '.profile-icon',
                        '[aria-label*="account"]',
                        '[aria-label*="profile"]',
                        'a[href*="/account"]',
                        'a[href*="/settings"]',
                        'button[aria-label*="menu"]'
                    ];

                    for (const selector of avatarSelectors) {
                        if (document.querySelector(selector)) {
                            return true;
                        }
                    }

                    // Check if there's a "Log out" or "Sign out" link/button
                    const links = document.querySelectorAll('a, button');
                    for (const el of links) {
                        const text = el.textContent.toLowerCase();
                        if (text.includes('log out') || text.includes('sign out') || text.includes('logout')) {
                            return true;
                        }
                    }

                    // Check for subscriber-only content being visible (no paywall modal)
                    const paywallSelectors = [
                        '[class*="paywall"]',
                        '[class*="subscribe-modal"]',
                        '[data-testid="paywall"]'
                    ];
                    const hasPaywall = paywallSelectors.some(s => document.querySelector(s));

                    // If we're on an article page and there's no paywall, likely logged in
                    const isArticle = document.querySelector('article') !== null;
                    if (isArticle && !hasPaywall) {
                        // Check article has substantial content (not truncated)
                        const articleText = document.querySelector('article')?.textContent || '';
                        if (articleText.length > 2000) {
                            return true;
                        }
                    }

                    return false;
                }
            """)

            return is_logged_in

        except Exception:
            return False

    async def authenticate_with_dispatch(self, page, browser_context):
        """Authenticate with The Dispatch using saved cookies or manual login"""
        if self.authenticated_with_dispatch:
            return True

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
                print("❌ Authentication verification failed")
                await self.save_dispatch_cookies(browser_context)
                print("⚠️ Proceeding anyway - cookies saved for future attempts")
                self.authenticated_with_dispatch = True
                return True

        except Exception as e:
            print(f"❌ Authentication error: {e}")
            print("🔧 Please complete authentication manually if needed")
            self.authenticated_with_dispatch = True
            return True

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