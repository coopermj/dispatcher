#!/usr/bin/env python3
"""
The Dispatch Email to PDF Converter with Persistent Browser and ReMarkable Upload
Enhanced with duplicate tracking to prevent reprocessing emails
Keeps browser open throughout the entire conversion process
Enhanced version that removes #app > header before PDF generation
Now uploads PDFs to ReMarkable News folder using rmapi
"""

import os
import re
import base64
import pickle
import asyncio
import json
import traceback
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
import hashlib

# Google API imports
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# HTML parsing
from bs4 import BeautifulSoup
import html2text


class DispatchPersistentConverter:
    def __init__(self, credentials_file='credentials.json', token_file='token.pickle',
                 cookies_file='dispatch_cookies.json', rmapi_path='~/rmapi/rmapi',
                 tracking_file='dispatch_email_tracking.json'):
        # Expanded scopes for both Gmail and user info
        self.SCOPES = [
            'https://www.googleapis.com/auth/gmail.readonly',
            'https://www.googleapis.com/auth/userinfo.email',
            'https://www.googleapis.com/auth/userinfo.profile',
            'openid'
        ]
        self.credentials_file = credentials_file
        self.token_file = token_file
        self.cookies_file = cookies_file
        self.tracking_file = tracking_file
        self.rmapi_path = os.path.expanduser(rmapi_path)
        self.service = None
        self.creds = None
        self.user_info = None
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = False
        self.h2t.ignore_images = False

        # Browser session variables
        self.browser = None
        self.page = None
        self.context = None
        self.authenticated = False

        # Tracking variables
        self.processed_emails = {}
        self.load_tracking_data()

    def load_tracking_data(self):
        """Load previously processed email tracking data"""
        try:
            if os.path.exists(self.tracking_file):
                with open(self.tracking_file, 'r') as f:
                    self.processed_emails = json.load(f)
                print(f"📊 Loaded tracking data: {len(self.processed_emails)} previously processed emails")
            else:
                self.processed_emails = {}
                print("📊 No previous tracking data found - starting fresh")
        except Exception as e:
            print(f"⚠️ Error loading tracking data: {e}")
            self.processed_emails = {}

    def save_tracking_data(self):
        """Save processed email tracking data"""
        try:
            with open(self.tracking_file, 'w') as f:
                json.dump(self.processed_emails, f, indent=2)
            print(f"💾 Saved tracking data: {len(self.processed_emails)} processed emails")
        except Exception as e:
            print(f"⚠️ Error saving tracking data: {e}")

    def get_email_fingerprint(self, email_data):
        """Create a unique fingerprint for an email based on multiple attributes"""
        # Use multiple attributes to create a robust fingerprint
        fingerprint_data = {
            'subject': email_data.get('subject', ''),
            'sender': email_data.get('sender', ''),
            'date': email_data.get('date', ''),
            'message_id': email_data.get('message_id', '')
        }

        # Create hash from the combined data
        fingerprint_string = json.dumps(fingerprint_data, sort_keys=True)
        return hashlib.md5(fingerprint_string.encode()).hexdigest()

    def is_email_processed(self, email_data):
        """Check if an email has been processed before"""
        fingerprint = self.get_email_fingerprint(email_data)
        return fingerprint in self.processed_emails

    def mark_email_processed(self, email_data, pdf_path, remarkable_uploaded=False):
        """Mark an email as processed and store metadata"""
        fingerprint = self.get_email_fingerprint(email_data)

        self.processed_emails[fingerprint] = {
            'subject': email_data.get('subject', ''),
            'sender': email_data.get('sender', ''),
            'date': email_data.get('date', ''),
            'message_id': email_data.get('message_id', ''),
            'processed_date': datetime.now().isoformat(),
            'pdf_path': pdf_path,
            'remarkable_uploaded': remarkable_uploaded,
            'fingerprint': fingerprint
        }

    def get_processed_count(self):
        """Get count of previously processed emails"""
        return len(self.processed_emails)

    def list_processed_emails(self, limit=10):
        """List recently processed emails"""
        if not self.processed_emails:
            return []

        # Sort by processed_date (most recent first)
        sorted_emails = sorted(
            self.processed_emails.values(),
            key=lambda x: x.get('processed_date', ''),
            reverse=True
        )

        return sorted_emails[:limit]

    def cleanup_tracking_data(self, output_dir):
        """Remove tracking entries for PDFs that no longer exist"""
        cleaned_count = 0
        to_remove = []

        for fingerprint, data in self.processed_emails.items():
            pdf_path = data.get('pdf_path', '')
            if pdf_path and not os.path.exists(pdf_path):
                to_remove.append(fingerprint)
                cleaned_count += 1

        for fingerprint in to_remove:
            del self.processed_emails[fingerprint]

        if cleaned_count > 0:
            print(f"🧹 Cleaned up {cleaned_count} entries for missing PDFs")
            self.save_tracking_data()

        return cleaned_count

    def check_rmapi_availability(self):
        """Check if rmapi is available and accessible"""
        try:
            if not os.path.exists(self.rmapi_path):
                print(f"❌ rmapi not found at: {self.rmapi_path}")
                print(f"💡 Please ensure rmapi is installed and the path is correct")
                return False

            # Test rmapi with a simple command
            result = subprocess.run([self.rmapi_path, 'ls'],
                                    capture_output=True, text=True, timeout=30)

            if result.returncode == 0:
                print(f"✅ rmapi is available at: {self.rmapi_path}")
                return True
            else:
                print(f"❌ rmapi test failed: {result.stderr}")
                print(f"💡 Please ensure rmapi is properly configured and authenticated")
                return False

        except subprocess.TimeoutExpired:
            print("❌ rmapi command timed out")
            return False
        except Exception as e:
            print(f"❌ Error checking rmapi: {e}")
            return False

    def upload_to_remarkable(self, pdf_path, remarkable_folder="News"):
        """Upload PDF to ReMarkable using rmapi"""
        try:
            pdf_path = Path(pdf_path)
            if not pdf_path.exists():
                print(f"❌ PDF file not found: {pdf_path}")
                return False

            print(f"📤 Uploading {pdf_path.name} to ReMarkable folder: {remarkable_folder}")

            # First, ensure the News folder exists
            print(f"📁 Checking/creating folder: {remarkable_folder}")
            mkdir_result = subprocess.run([self.rmapi_path, 'mkdir', remarkable_folder],
                                          capture_output=True, text=True, timeout=30)

            # mkdir will fail if folder already exists, which is fine
            if mkdir_result.returncode != 0 and "already exists" not in mkdir_result.stderr:
                print(f"⚠️ mkdir result: {mkdir_result.stderr}")

            # Upload the file to the News folder
            upload_cmd = [self.rmapi_path, 'put', str(pdf_path), remarkable_folder]
            print(f"🔧 Running: {' '.join(upload_cmd)}")

            result = subprocess.run(upload_cmd, capture_output=True, text=True, timeout=60)

            if result.returncode == 0:
                print(f"✅ Successfully uploaded {pdf_path.name} to ReMarkable/{remarkable_folder}")
                return True
            else:
                print(f"❌ Upload failed: {result.stderr}")
                print(f"📤 stdout: {result.stdout}")
                return False

        except subprocess.TimeoutExpired:
            print("❌ Upload command timed out")
            return False
        except Exception as e:
            print(f"❌ Error uploading to ReMarkable: {e}")
            return False

    def authenticate(self):
        """Authenticate with Google (for both Gmail and The Dispatch)"""
        print("🔐 Authenticating with Google...")
        creds = None

        if os.path.exists(self.token_file):
            with open(self.token_file, 'rb') as token:
                creds = pickle.load(token)

        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"🔧 Token refresh failed: {e}")
                    creds = None

            if not creds:
                if not os.path.exists(self.credentials_file):
                    print(f"❌ Please download Google OAuth credentials as '{self.credentials_file}'")
                    return False

                flow = InstalledAppFlow.from_client_secrets_file(
                    self.credentials_file, self.SCOPES)
                creds = flow.run_local_server(port=8080)

            with open(self.token_file, 'wb') as token:
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

    def search_dispatch_emails(self, max_results=10):
        """Search for emails from The Dispatch"""
        try:
            query = 'from:@thedispatch.com'
            results = self.service.users().messages().list(
                userId='me', q=query, maxResults=max_results
            ).execute()

            messages = results.get('messages', [])
            print(f"📧 Found {len(messages)} emails from The Dispatch")
            return messages

        except Exception as e:
            print(f"❌ Error searching emails: {e}")
            return []

    def get_message_content(self, message_id):
        """Get full message content"""
        try:
            message = self.service.users().messages().get(
                userId='me', id=message_id, format='full'
            ).execute()
            return message
        except Exception as e:
            print(f"❌ Error getting message {message_id}: {e}")
            return None

    def extract_body(self, payload):
        """Extract email body from payload"""
        body = ""

        if 'parts' in payload:
            for part in payload['parts']:
                if part['mimeType'] == 'text/html':
                    if 'data' in part['body']:
                        data = part['body']['data']
                        body = base64.urlsafe_b64decode(data).decode('utf-8')
                        break
                elif part['mimeType'] == 'multipart/alternative':
                    body = self.extract_body(part)
                    if body:
                        break
        elif payload['mimeType'] == 'text/html':
            if 'data' in payload['body']:
                data = payload['body']['data']
                body = base64.urlsafe_b64decode(data).decode('utf-8')

        return body

    def extract_email_data(self, message):
        """Extract structured data from email message"""
        try:
            headers = {h['name']: h['value'] for h in message['payload'].get('headers', [])}

            subject = headers.get('Subject', 'No Subject')
            sender = headers.get('From', 'Unknown Sender')
            date = headers.get('Date', 'Unknown Date')

            raw_body = self.extract_body(message['payload'])
            is_html = bool(raw_body and ('<html' in raw_body.lower() or '<div' in raw_body.lower()))

            if is_html:
                text_body = self.h2t.handle(raw_body)
            else:
                text_body = raw_body

            return {
                'subject': subject,
                'sender': sender,
                'date': date,
                'body': text_body,
                'raw_body': raw_body,
                'is_html': is_html,
                'message_id': message['id']
            }

        except Exception as e:
            print(f"❌ Error extracting email data: {e}")
            return None

    def extract_read_online_url(self, email_data):
        """Extract 'Read Online' URL from email"""
        if not email_data.get('raw_body'):
            return None

        try:
            soup = BeautifulSoup(email_data['raw_body'], 'html.parser')

            for link in soup.find_all('a', href=True):
                link_text = link.get_text().strip().lower()
                href = link['href']

                if ('read online' in link_text or
                        'view in browser' in link_text or
                        'open in browser' in link_text):
                    if 'thedispatch.com' in href:
                        return href

            return None
        except Exception as e:
            print(f"❌ Error extracting read online URL: {e}")
            return None

    async def save_cookies(self):
        """Save browser cookies to file"""
        try:
            if not self.context:
                print("⚠️ No browser context available for saving cookies")
                return False

            cookies = await self.context.cookies()

            # Filter for The Dispatch cookies
            dispatch_cookies = [
                cookie for cookie in cookies
                if 'thedispatch.com' in cookie.get('domain', '')
            ]

            if dispatch_cookies:
                with open(self.cookies_file, 'w') as f:
                    json.dump(dispatch_cookies, f, indent=2)
                print(f"✅ Saved {len(dispatch_cookies)} cookies to {self.cookies_file}")
                return True
            else:
                print("⚠️ No Dispatch cookies found to save")
                return False

        except Exception as e:
            print(f"❌ Error saving cookies: {e}")
            return False

    async def load_cookies(self):
        """Load browser cookies from file"""
        try:
            if not os.path.exists(self.cookies_file):
                print(f"📝 No saved cookies found at {self.cookies_file}")
                return False

            if not self.context:
                print("⚠️ No browser context available for loading cookies")
                return False

            with open(self.cookies_file, 'r') as f:
                cookies = json.load(f)

            if cookies:
                await self.context.add_cookies(cookies)
                print(f"✅ Loaded {len(cookies)} cookies from {self.cookies_file}")
                return True
            else:
                print("⚠️ No cookies found in file")
                return False

        except Exception as e:
            print(f"❌ Error loading cookies: {e}")
            return False

    async def test_authentication(self):
        """Test if we're already authenticated by checking a protected page"""
        try:
            print("🔍 Testing existing authentication...")

            # Try to access the homepage first and check for login indicators
            await self.page.goto('https://thedispatch.com', timeout=30000)
            await asyncio.sleep(3)

            current_url = self.page.url
            page_content = await self.page.content()

            # Look for indicators that we're logged in
            logged_in_indicators = [
                'account',
                'subscription',
                'profile',
                'logout',
                'sign out',
                'settings',
                'subscriber',
                'my account'
            ]

            # Look for login indicators (meaning we're NOT logged in)
            login_indicators = [
                'sign in',
                'login',
                'log in',
                'subscribe',
                'get started'
            ]

            has_login_indicators = any(indicator in page_content.lower() for indicator in login_indicators)
            has_logged_in_indicators = any(indicator in page_content.lower() for indicator in logged_in_indicators)

            # If we have logout/account indicators and no login prompts, we're likely logged in
            if has_logged_in_indicators and not has_login_indicators:
                print("✅ Already authenticated with saved cookies!")
                self.authenticated = True
                return True
            else:
                print("❌ Not authenticated - need to log in")
                return False

        except Exception as e:
            print(f"⚠️ Error testing authentication: {e}")
            return False

    async def save_html_snapshot(self, filename_suffix, url=""):
        """Save current page HTML for debugging"""
        try:
            content = await self.page.content()

            # Create debug directory if it doesn't exist
            debug_dir = Path("debug_html")
            debug_dir.mkdir(exist_ok=True)

            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"debug_html/page_{timestamp}_{filename_suffix}.html"

            with open(filename, 'w', encoding='utf-8') as f:
                f.write(content)

            print(f"📄 HTML snapshot saved: {filename}")
            if url:
                print(f"🔗 URL: {url}")

            return filename

        except Exception as e:
            print(f"⚠️ Error saving HTML snapshot: {e}")
            return None

    async def start_browser_session(self):
        """Start persistent browser session"""
        try:
            from playwright.async_api import async_playwright

            print("🌐 Starting persistent browser session...")
            self.playwright = async_playwright()
            self.p = await self.playwright.start()

            self.browser = await self.p.chromium.launch(
                headless=False,
                args=['--no-first-run', '--no-default-browser-check']
            )

            self.context = await self.browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
            )

            self.page = await self.context.new_page()
            print("✅ Browser session started")
            return True

        except ImportError:
            print("❌ Playwright not installed. Install with: pip install playwright && playwright install")
            return False
        except Exception as e:
            print(f"❌ Error starting browser: {e}")
            return False

    async def authenticate_with_dispatch(self):
        """Authenticate with The Dispatch using saved cookies or magic link"""
        if self.authenticated:
            return True

        try:
            print("🔐 Authenticating with The Dispatch...")

            # First, try to load existing cookies
            cookies_loaded = await self.load_cookies()

            if cookies_loaded:
                # Test if the loaded cookies work
                if await self.test_authentication():
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
            print("4. Come back here and press ENTER when you're logged in")
            print("5. Your session will be saved for future runs")
            print("=" * 50)

            # Open The Dispatch homepage
            await self.page.goto('https://thedispatch.com', timeout=30000)
            await asyncio.sleep(2)

            # Wait for user to complete authentication
            print("\n⏳ Waiting for you to complete login...")
            input("✋ Press ENTER after you've completed the login process: ")

            # Test authentication after manual login
            if await self.test_authentication():
                # Save cookies for future use
                await self.save_cookies()
                print("✅ Authentication successful and cookies saved!")
                self.authenticated = True
                return True
            else:
                print("❌ Authentication verification failed")
                # Try to save cookies anyway in case our test was wrong
                await self.save_cookies()
                print("⚠️ Proceeding anyway - cookies saved for future attempts")
                self.authenticated = True
                return True

        except Exception as e:
            print(f"❌ Authentication error: {e}")
            print("🔧 Please complete authentication manually if needed")
            input("Press ENTER after you've logged in: ")
            self.authenticated = True
            return True

    async def remove_header_elements(self):
        """Remove header elements and navigation/accessibility elements specifically for The Dispatch"""
        try:
            print("🧹 Removing The Dispatch header and navigation elements...")

            # Enhanced JavaScript to remove The Dispatch specific elements
            header_removal_script = """
            (() => {
                // Remove by tag (header, nav, footer, aside)
                ['header','nav','footer','aside'].forEach(tag => {
                    document.querySelectorAll(tag).forEach(e => e.remove());
                });

                // Remove elements by class or id (but not 'fixed' or 'sticky')
                const keywords = [
                    'navbar','banner','paywall','newsletter','comment','breadcrumb',
                    'subscribe','sidebar','popup','ad','site-footer','site-info','z-scroll-to','primary-button'
                ];
                keywords.forEach(word => {
                    document.querySelectorAll(`[class*="${word}"], [id*="${word}"]`).forEach(e => e.remove());
                });

                // Remove overlays or popups by computed style (only if width/height covers most of viewport)
                document.querySelectorAll('*').forEach(e => {
                    const s = window.getComputedStyle(e);
                    if ((s.position === 'fixed' || s.position === 'sticky') &&
                        e.offsetHeight > window.innerHeight * 0.5 &&
                        e.offsetWidth > window.innerWidth * 0.5) {
                        e.remove();
                    }
                });

                // Restore body/main/article margins if you want, but DO NOT remove <style> or <link>!
                const main = document.querySelector('main, article, [role=main]');
                if (main) {
                    main.style.marginTop = '0px';
                    main.style.paddingTop = '0px';
                }
            })();
            """

            # Execute the enhanced script
            result = await self.page.evaluate(header_removal_script)

            # Wait for DOM changes and reflow
            await asyncio.sleep(2)

            # Log the results
            print(f"✅ Removed header/navigation elements")

            # Scroll to top to ensure we start from the beginning
            await self.page.evaluate('window.scrollTo(0, 0);')
            await asyncio.sleep(1)

            return True

        except Exception as e:
            print(f"⚠️ Error removing header elements: {e}")
            print(f"🔧 Debug info: {traceback.format_exc()}")
            # Continue anyway - this shouldn't stop PDF generation
            return True

    async def convert_url_to_pdf(self, url, output_filename):
        """Convert URL to PDF using persistent session with header removal"""
        try:
            print(f"🔗 Navigating to: {url}")

            # Navigate to the newsletter URL
            await self.page.goto(url, timeout=30000)
            await asyncio.sleep(3)

            # Check page content
            title = await self.page.title()
            print(f"📄 Page title: {title}")

            # Wait for content to load
            try:
                await self.page.wait_for_selector('article, .article, .post, .content, main', timeout=10000)
                print("✅ Content loaded")
            except:
                print("⚠️ Standard content selectors not found, but continuing...")

            await asyncio.sleep(2)

            # Save HTML before any modifications
            await self.save_html_snapshot("before_cleanup", url)

            # Remove header elements before PDF generation
            await self.remove_header_elements()

            # Save HTML after header removal
            await self.save_html_snapshot("after_cleanup", url)

            # Generate PDF
            print(f"📄 Generating PDF: {output_filename}")
            await self.page.pdf(
                path=output_filename,
                format='A4',
                margin={'top': '0.75in', 'right': '0.75in', 'bottom': '0.75in', 'left': '0.75in'},
                print_background=True,
                prefer_css_page_size=False
            )

            # Verify PDF was created
            if os.path.exists(output_filename) and os.path.getsize(output_filename) > 5000:
                print(f"✅ PDF created successfully: {output_filename}")
                return True
            else:
                print("❌ PDF creation failed or file too small")
                return False

        except Exception as e:
            print(f"❌ Error converting URL: {e}")
            return False

    async def close_browser_session(self):
        """Close the browser session"""
        try:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'p'):
                await self.p.stop()
            print("🔒 Browser session closed")
        except Exception as e:
            print(f"⚠️ Error closing browser: {e}")

    def sanitize_filename(self, filename):
        """Create safe filename"""
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'[-\s]+', '-', filename)
        return filename[:100]

    def print_tracking_summary(self):
        """Print a summary of tracking status"""
        print("\n📊 TRACKING SUMMARY")
        print("=" * 50)
        total_processed = self.get_processed_count()
        print(f"📝 Total emails processed: {total_processed}")

        if total_processed > 0:
            print("\n📋 Recently processed emails:")
            recent = self.list_processed_emails(5)
            for i, email in enumerate(recent, 1):
                print(f"  {i}. {email.get('subject', 'No Subject')[:50]}...")
                print(f"     📅 {email.get('processed_date', 'Unknown date')}")
                print(f"     📤 ReMarkable: {'✅' if email.get('remarkable_uploaded') else '❌'}")
        print("=" * 50)

    async def process_emails(self, output_dir='dispatch_persistent_pdfs', max_emails=5,
                             upload_to_remarkable=True, force_reprocess=False):
        """Main processing function with persistent browser and ReMarkable upload"""
        print("🚀 Starting Dispatch Email Converter with Persistent Browser & ReMarkable Upload")
        print("📊 Enhanced with duplicate tracking and prevention")
        print("=" * 70)

        # Show tracking summary
        self.print_tracking_summary()

        # Clean up tracking data (remove entries for missing PDFs)
        self.cleanup_tracking_data(output_dir)

        # Check rmapi availability if upload is requested
        if upload_to_remarkable:
            if not self.check_rmapi_availability():
                print("⚠️ ReMarkable upload disabled due to rmapi issues")
                upload_to_remarkable = False

        try:
            # Step 1: Authenticate with Google
            if not self.authenticate():
                return

            # Step 2: Start browser session
            if not await self.start_browser_session():
                return

            # Step 3: Authenticate with The Dispatch (one time)
            if not await self.authenticate_with_dispatch():
                await self.close_browser_session()
                return

            # Step 4: Create output directory
            Path(output_dir).mkdir(exist_ok=True)

            # Step 5: Get email list
            messages = self.search_dispatch_emails(max_emails)
            if not messages:
                print("❌ No emails found")
                await self.close_browser_session()
                return

            # Step 6: Process each email (browser stays open)
            success_count = 0
            uploaded_count = 0
            skipped_count = 0

            for i, message in enumerate(messages, 1):
                print(f"\n📄 Processing email {i}/{len(messages)}...")

                # Get email content
                email_msg = self.get_message_content(message['id'])
                if not email_msg:
                    continue

                # Extract email data
                email_data = self.extract_email_data(email_msg)
                if not email_data:
                    continue

                print(f"📧 Subject: {email_data['subject']}")

                # Check if already processed (unless force reprocess is enabled)
                if not force_reprocess and self.is_email_processed(email_data):
                    fingerprint = self.get_email_fingerprint(email_data)
                    processed_data = self.processed_emails[fingerprint]
                    print(f"⏭️  SKIPPED - Already processed on {processed_data.get('processed_date', 'unknown date')}")
                    print(f"📁 Existing PDF: {processed_data.get('pdf_path', 'unknown path')}")
                    print(
                        f"📤 ReMarkable: {'✅ Uploaded' if processed_data.get('remarkable_uploaded') else '❌ Not uploaded'}")
                    skipped_count += 1
                    continue

                # Create filename
                safe_subject = self.sanitize_filename(email_data['subject'])
                filename = f"{output_dir}/dispatch_{i:03d}_{safe_subject}.pdf"

                # Get Read Online URL
                read_online_url = self.extract_read_online_url(email_data)

                if read_online_url:
                    print(f"🔗 Found Read Online URL: {read_online_url}")
                    success = await self.convert_url_to_pdf(read_online_url, filename)

                    if success:
                        success_count += 1
                        print(f"✅ Successfully converted: {filename}")

                        # Upload to ReMarkable if enabled
                        remarkable_uploaded = False
                        if upload_to_remarkable:
                            upload_success = self.upload_to_remarkable(filename, "News")
                            if upload_success:
                                uploaded_count += 1
                                remarkable_uploaded = True
                            else:
                                print(f"⚠️ Failed to upload {filename} to ReMarkable")

                        # Mark as processed in tracking
                        self.mark_email_processed(email_data, filename, remarkable_uploaded)
                        self.save_tracking_data()
                        print(f"💾 Email marked as processed in tracking")

                    else:
                        print(f"❌ Failed to convert")
                else:
                    print("❌ No Read Online URL found, skipping...")

                # Small delay between conversions
                await asyncio.sleep(1)

            print(f"\n🎉 Email conversion complete!")
            print(f"✅ Successfully converted: {success_count}/{len(messages)} emails")
            print(f"⏭️  Skipped (already processed): {skipped_count}/{len(messages)} emails")
            print(f"📁 Check the '{output_dir}' directory for PDFs")

            if upload_to_remarkable:
                print(f"📤 Successfully uploaded to ReMarkable: {uploaded_count}/{success_count} PDFs")
                if uploaded_count > 0:
                    print(f"📱 Check your ReMarkable's 'News' folder for the uploaded files")

            # Final tracking summary
            self.print_tracking_summary()

        finally:
            # Always close browser session
            await self.close_browser_session()


async def run_email_converter():
    """Entry point for calling from main.py or other external callers"""
    converter = DispatchPersistentConverter(
        rmapi_path='~/rmapi/rmapi'
    )
    await converter.process_emails(
        output_dir='dispatch_persistent_pdfs',
        max_emails=5,
        upload_to_remarkable=True,
        force_reprocess=False
    )


async def main():
    """Main function"""
    try:
        await run_email_converter()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        print(f"🔧 DEBUG: {traceback.format_exc()}")


if __name__ == "__main__":
    print("🚀 THE DISPATCH EMAIL CONVERTER + REMARKABLE UPLOAD")
    print("📊 Enhanced with duplicate tracking and prevention")
    print("=" * 65)

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
