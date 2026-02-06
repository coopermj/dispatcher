#!/usr/bin/env python3
"""
Browser manager for Playwright automation and PDF generation
"""

import asyncio
import os
import traceback
from datetime import datetime
from pathlib import Path

from config.settings import (
    BROWSER_USER_AGENT, BROWSER_HEADLESS, BROWSER_TIMEOUT,
    PDF_FORMAT, PDF_MARGINS, CONTENT_SELECTORS, HEADER_REMOVAL_KEYWORDS,
    DEBUG_DIR, CONTENT_LOAD_WAIT, MIN_PDF_SIZE_BYTES
)


class BrowserManager:
    """Manages browser automation and PDF generation"""
    
    def __init__(self):
        self.browser = None
        self.page = None
        self.context = None
        self.playwright = None
        self.p = None
    
    async def start_browser_session(self):
        """Start persistent browser session - exact copy from tester.py"""
        try:
            from playwright.async_api import async_playwright

            print("🌐 Starting persistent browser session...")
            self.playwright = async_playwright()
            self.p = await self.playwright.start()

            # On macOS, position window off-screen to prevent focus stealing in headed mode
            launch_args = ['--no-first-run', '--no-default-browser-check']
            if not BROWSER_HEADLESS:
                launch_args.append('--window-position=-2400,-2400')

            self.browser = await self.p.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=launch_args
            )

            self.context = await self.browser.new_context(
                user_agent=BROWSER_USER_AGENT
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

    async def close_browser_session(self):
        """Close the browser session"""
        try:
            if self.browser:
                await self.browser.close()
            if hasattr(self, 'p') and self.p:
                await self.p.stop()
            print("🔒 Browser session closed")
        except Exception as e:
            print(f"⚠️ Error closing browser: {e}")

    async def save_html_snapshot(self, filename_suffix, url=""):
        """Save current page HTML for debugging - exact copy from tester.py"""
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

    async def remove_header_elements(self):
        """Remove header elements and navigation/accessibility elements - exact copy from tester.py"""
        try:
            print("🧹 Removing The Dispatch header and navigation elements...")

            # Enhanced JavaScript to remove The Dispatch specific elements (exact copy from tester.py)
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

            # Debug: Print the exact JavaScript being executed
            print("🔧 Executing JavaScript:")
            print("=" * 40)
            print(header_removal_script[:200] + "..." + header_removal_script[-200:])
            print("=" * 40)

            # Execute the enhanced script
            result = await self.page.evaluate(header_removal_script)

            # Wait for DOM changes and reflow
            await asyncio.sleep(2)

            print("✅ Removed header/navigation elements")

            # Scroll to top to ensure we start from the beginning
            await self.page.evaluate('window.scrollTo(0, 0);')
            await asyncio.sleep(1)

            return True

        except Exception as e:
            print(f"⚠️ Error removing header elements: {e}")
            print(f"🔧 Debug info: {traceback.format_exc()}")
            # Continue anyway - this shouldn't stop PDF generation
            return True

    async def wait_for_content(self):
        """Wait for page content to load"""
        try:
            # Wait for content to load
            await self.page.wait_for_selector(
                ', '.join(CONTENT_SELECTORS), 
                timeout=10000
            )
            print("✅ Content loaded")
            return True
        except:
            print("⚠️ Standard content selectors not found, but continuing...")
            return False

    async def navigate_to_url(self, url):
        """Navigate to a URL and wait for content"""
        try:
            print(f"🔗 Navigating to: {url}")

            # Navigate to the newsletter URL
            await self.page.goto(url, timeout=BROWSER_TIMEOUT)
            await asyncio.sleep(CONTENT_LOAD_WAIT)

            # Check page content
            title = await self.page.title()
            print(f"📄 Page title: {title}")

            # Wait for content to load
            await self.wait_for_content()
            await asyncio.sleep(2)

            return True

        except Exception as e:
            print(f"❌ Error navigating to URL: {e}")
            return False

    async def generate_pdf(self, output_filename):
        """Generate PDF from current page"""
        try:
            print(f"📄 Generating PDF: {output_filename}")
            
            await self.page.pdf(
                path=output_filename,
                format=PDF_FORMAT,
                margin=PDF_MARGINS,
                print_background=True,
                prefer_css_page_size=False
            )

            # Verify PDF was created
            if os.path.exists(output_filename) and os.path.getsize(output_filename) > MIN_PDF_SIZE_BYTES:
                print(f"✅ PDF created successfully: {output_filename}")
                return True
            else:
                print("❌ PDF creation failed or file too small")
                return False

        except Exception as e:
            print(f"❌ Error generating PDF: {e}")
            return False

    async def convert_url_to_pdf(self, url, output_filename):
        """Convert URL to PDF using persistent session with header removal - exact copy from tester.py"""
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
            print(f"❌ Error converting URL to PDF: {e}")
            return False

    def get_page(self):
        """Get the current page object"""
        return self.page
    
    def get_context(self):
        """Get the current browser context"""
        return self.context
    
    def is_session_active(self):
        """Check if browser session is active"""
        return self.browser is not None and self.page is not None

    async def create_new_page(self):
        """Create a new page in the current context for parallel operations"""
        if not self.context:
            print("❌ Cannot create new page - no browser context")
            return None
        try:
            new_page = await self.context.new_page()
            return new_page
        except Exception as e:
            print(f"❌ Error creating new page: {e}")
            return None

    async def close_page(self, page):
        """Close a specific page"""
        if page and page != self.page:  # Don't close the main page
            try:
                await page.close()
            except Exception as e:
                print(f"⚠️ Error closing page: {e}")

    async def convert_url_to_pdf_with_page(self, url, output_filename, page=None):
        """Convert URL to PDF using a specific page (for parallel operations)"""
        use_page = page or self.page
        try:
            # Check if we're already on the correct URL (to preserve DOM modifications like link rewrites)
            current_url = use_page.url
            # Normalize URLs for comparison (strip scheme, trailing slashes, and compare base path)
            def normalize_url(u):
                if not u:
                    return ''
                u = u.rstrip('/')
                # Remove scheme for comparison
                if u.startswith('https://'):
                    u = u[8:]
                elif u.startswith('http://'):
                    u = u[7:]
                # Remove query params and fragments for base comparison
                u = u.split('?')[0].split('#')[0]
                return u.rstrip('/')

            if current_url and normalize_url(current_url) == normalize_url(url):
                print(f"📄 Already on page: {url} (preserving DOM modifications)")
            else:
                print(f"🔗 Navigating to: {url}")
                # Navigate to the URL
                await use_page.goto(url, timeout=30000)
                # Wait for page to be stable
                try:
                    await use_page.wait_for_load_state('networkidle', timeout=10000)
                except:
                    pass  # Continue even if timeout
                await asyncio.sleep(2)

            # Check page content
            title = await use_page.title()
            print(f"📄 Page title: {title}")

            # Wait for content to load
            try:
                await use_page.wait_for_selector('article, .article, .post, .content, main', timeout=10000)
                print("✅ Content loaded")
            except:
                print("⚠️ Standard content selectors not found, but continuing...")

            await asyncio.sleep(1)

            # Remove header elements before PDF generation
            await self.remove_header_elements_from_page(use_page)

            # Generate PDF
            print(f"📄 Generating PDF: {output_filename}")
            await use_page.pdf(
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
            print(f"❌ Error converting URL to PDF: {e}")
            return False

    async def remove_header_elements_from_page(self, page):
        """Remove header elements from a specific page"""
        try:
            # Safer removal script that preserves main content
            header_removal_script = """
            (() => {
                // First, identify the main content area and protect it
                const mainContent = document.querySelector('article, main, [role="main"], .post-content, .article-content, .entry-content');

                // Remove navigation elements by tag (but not if they're inside main content)
                ['header', 'nav', 'footer'].forEach(tag => {
                    document.querySelectorAll(tag).forEach(e => {
                        // Don't remove if it's inside the main content area
                        if (mainContent && mainContent.contains(e)) return;
                        e.remove();
                    });
                });

                // Remove specific UI elements that are clearly not content
                // Note: removed 'newsletter' and 'comment' from keywords as they can match content
                const keywords = [
                    'navbar', 'banner', 'paywall', 'breadcrumb',
                    'subscribe-modal', 'sidebar', 'popup', 'site-footer',
                    'site-info', 'z-scroll-to', 'primary-button', 'sticky-header'
                ];
                keywords.forEach(word => {
                    document.querySelectorAll(`[class*="${word}"], [id*="${word}"]`).forEach(e => {
                        // Don't remove if it's inside the main content area
                        if (mainContent && mainContent.contains(e)) return;
                        // Don't remove the main content area itself
                        if (e === mainContent) return;
                        e.remove();
                    });
                });

                // Remove large fixed/sticky overlays (like paywalls)
                document.querySelectorAll('*').forEach(e => {
                    // Skip the main content area
                    if (mainContent && (mainContent.contains(e) || e.contains(mainContent))) return;

                    const s = window.getComputedStyle(e);
                    if ((s.position === 'fixed' || s.position === 'sticky') &&
                        e.offsetHeight > window.innerHeight * 0.5 &&
                        e.offsetWidth > window.innerWidth * 0.5) {
                        e.remove();
                    }
                });

                // Adjust margins on main content
                if (mainContent) {
                    mainContent.style.marginTop = '0px';
                    mainContent.style.paddingTop = '20px';
                }

                return mainContent ? 'Main content found' : 'No main content identified';
            })();
            """
            result = await page.evaluate(header_removal_script)
            print(f"🧹 Header cleanup: {result}")
            await asyncio.sleep(1)
            await page.evaluate('window.scrollTo(0, 0);')
            return True
        except Exception as e:
            print(f"⚠️ Error removing header elements: {e}")
            return True

    async def navigate_to_url_with_page(self, url, page=None):
        """Navigate to a URL using a specific page"""
        use_page = page or self.page
        try:
            print(f"🔗 Navigating to: {url}")
            await use_page.goto(url, timeout=BROWSER_TIMEOUT)
            await asyncio.sleep(CONTENT_LOAD_WAIT)

            title = await use_page.title()
            print(f"📄 Page title: {title}")

            try:
                await use_page.wait_for_selector(
                    ', '.join(CONTENT_SELECTORS),
                    timeout=10000
                )
                print("✅ Content loaded")
            except:
                print("⚠️ Standard content selectors not found, but continuing...")

            # Wait for page to be fully stable (network idle)
            try:
                await use_page.wait_for_load_state('networkidle', timeout=5000)
            except:
                pass  # Continue even if timeout

            await asyncio.sleep(1)
            return True

        except Exception as e:
            print(f"❌ Error navigating to URL: {e}")
            return False
