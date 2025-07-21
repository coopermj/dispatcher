#!/usr/bin/env python3
"""
Browser manager for Playwright automation and PDF generation
"""

import asyncio
import os
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
        """Start persistent browser session"""
        try:
            from playwright.async_api import async_playwright

            print("🌐 Starting persistent browser session...")
            self.playwright = async_playwright()
            self.p = await self.playwright.start()

            self.browser = await self.p.chromium.launch(
                headless=BROWSER_HEADLESS,
                args=['--no-first-run', '--no-default-browser-check']
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
        """Save current page HTML for debugging"""
        try:
            if not self.page:
                print("⚠️ No page available for HTML snapshot")
                return None
                
            content = await self.page.content()

            # Create debug directory if it doesn't exist
            DEBUG_DIR.mkdir(exist_ok=True)

            # Create filename with timestamp
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = DEBUG_DIR / f"page_{timestamp}_{filename_suffix}.html"

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
        """Remove header elements and navigation/accessibility elements specifically for The Dispatch"""
        try:
            print("🧹 Removing The Dispatch header and navigation elements...")

            # Enhanced JavaScript to remove The Dispatch specific elements
            header_removal_script = f"""
            (() => {{
                // Remove by tag (header, nav, footer, aside)
                ['header','nav','footer','aside'].forEach(tag => {{
                    document.querySelectorAll(tag).forEach(e => e.remove());
                }});

                // Remove elements by class or id keywords
                const keywords = {HEADER_REMOVAL_KEYWORDS};
                keywords.forEach(word => {{
                    document.querySelectorAll(`[class*="${{word}}"], [id*="${{word}}"]`).forEach(e => e.remove());
                }});

                // Remove overlays or popups by computed style (only if width/height covers most of viewport)
                document.querySelectorAll('*').forEach(e => {{
                    const s = window.getComputedStyle(e);
                    if ((s.position === 'fixed' || s.position === 'sticky') &&
                        e.offsetHeight > window.innerHeight * 0.5 &&
                        e.offsetWidth > window.innerWidth * 0.5) {{
                        e.remove();
                    }}
                }});

                // Restore body/main/article margins
                const main = document.querySelector('main, article, [role=main]');
                if (main) {{
                    main.style.marginTop = '0px';
                    main.style.paddingTop = '0px';
                }}
            }})();
            """

            # Execute the enhanced script
            await self.page.evaluate(header_removal_script)

            # Wait for DOM changes and reflow
            await asyncio.sleep(2)

            print("✅ Removed header/navigation elements")

            # Scroll to top to ensure we start from the beginning
            await self.page.evaluate('window.scrollTo(0, 0);')
            await asyncio.sleep(1)

            return True

        except Exception as e:
            print(f"⚠️ Error removing header elements: {e}")
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
        """Convert URL to PDF using persistent session with header removal"""
        try:
            # Navigate to URL
            if not await self.navigate_to_url(url):
                return False

            # Save HTML before any modifications
            await self.save_html_snapshot("before_cleanup", url)

            # Remove header elements before PDF generation
            await self.remove_header_elements()

            # Save HTML after header removal
            await self.save_html_snapshot("after_cleanup", url)

            # Generate PDF
            return await self.generate_pdf(output_filename)

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
