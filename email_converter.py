#!/usr/bin/env python3
"""
The Dispatch Email → PDF → reMarkable pipeline.

Thin orchestrator over the shared modules/ components. It scans Gmail for The
Dispatch newsletters (last 7 days, via GMAIL_SEARCH_QUERY), converts each
"Read Online" page to a PDF, and uploads new ones to the reMarkable News folder.

All heavy lifting is delegated to the shared modules so this pipeline and the
website pipeline (main.py) stay in sync:
  - Google OAuth + Dispatch login → modules.auth.AuthManager
  - Gmail search / body / read-online URL → modules.email_handler.EmailHandler
  - browser session, header removal, PDF conversion → modules.browser_manager.BrowserManager
  - duplicate tracking → modules.tracking.TrackingManager
  - reMarkable upload + live-inventory dedup → modules.remarkable.ReMarkableManager
"""

import asyncio
import traceback
from pathlib import Path

from modules import (
    AuthManager, EmailHandler, BrowserManager, TrackingManager, ReMarkableManager,
    LinkProcessor
)
from modules.utils import create_safe_pdf_filename
from config.settings import (
    DEFAULT_RMAPI_PATH, DEFAULT_MAX_EMAILS, DEFAULT_UPLOAD_TO_REMARKABLE,
    DEFAULT_FORCE_REPROCESS, DISPATCH_EMAIL_TRACKING_FILE, TRACKING_FILE,
    FOLLOW_ARTICLE_LINKS,
)


class DispatchPersistentConverter:
    """Email pipeline orchestrator (Gmail → PDF → reMarkable)."""

    def __init__(self, rmapi_path=None):
        self.auth_manager = AuthManager()
        self.email_handler = EmailHandler(self.auth_manager)
        self.browser_manager = BrowserManager()
        # Email pipeline keeps its own tracking file, separate from the website pipeline.
        self.tracking_manager = TrackingManager(tracking_file=DISPATCH_EMAIL_TRACKING_FILE)
        self.remarkable_manager = ReMarkableManager(rmapi_path or DEFAULT_RMAPI_PATH)

        # URLs already converted by the website pipeline — skip them here to avoid
        # producing the same article twice across the two pipelines.
        web_tracking = TrackingManager(tracking_file=TRACKING_FILE)
        self.web_processed_urls = web_tracking.get_processed_urls()
        if self.web_processed_urls:
            print(f"🔗 Loaded {len(self.web_processed_urls)} URLs from web scanner (cross-dedup)")

    def is_url_already_processed_by_web(self, url):
        """True if the website pipeline already produced a PDF for this URL."""
        return url in self.web_processed_urls

    async def process_emails(self, output_dir='dispatch_persistent_pdfs', max_emails=None,
                             upload_to_remarkable=None, force_reprocess=None):
        """Scan Gmail, convert newsletters to PDF, and upload new ones to reMarkable."""
        max_emails = max_emails if max_emails is not None else DEFAULT_MAX_EMAILS
        upload_to_remarkable = (upload_to_remarkable if upload_to_remarkable is not None
                                else DEFAULT_UPLOAD_TO_REMARKABLE)
        force_reprocess = force_reprocess if force_reprocess is not None else DEFAULT_FORCE_REPROCESS

        print("🚀 Starting Dispatch Email Converter (Gmail → PDF → reMarkable)")
        print("=" * 70)

        self.tracking_manager.print_tracking_summary()
        self.tracking_manager.cleanup_tracking_data()

        if upload_to_remarkable and not self.remarkable_manager.is_available():
            print("⚠️ ReMarkable upload disabled due to rmapi issues")
            upload_to_remarkable = False

        try:
            # Step 1: Google auth (Gmail + user info for The Dispatch)
            if not self.auth_manager.authenticate_google():
                print("❌ Google authentication failed")
                return

            # Step 2: Browser session
            if not await self.browser_manager.start_browser_session():
                print("❌ Browser session failed to start")
                return

            page = self.browser_manager.get_page()
            context = self.browser_manager.get_context()

            # Step 3: The Dispatch login (cookies or manual)
            if not await self.auth_manager.authenticate_with_dispatch(page, context):
                print("❌ The Dispatch authentication failed")
                await self.browser_manager.close_browser_session()
                return

            # Step 4: Output directory (absolute, so tracking paths are portable)
            output_path = Path(output_dir).resolve()
            output_path.mkdir(exist_ok=True)

            # Step 5: Pre-fetch the reMarkable inventory once for upload dedup
            if upload_to_remarkable:
                self.remarkable_manager.refresh_inventory()

            # Step 6: Get the recent email list (GMAIL_SEARCH_QUERY scopes to last 7 days)
            messages = self.email_handler.search_dispatch_emails(max_emails)
            if not messages:
                print("❌ No emails found")
                await self.browser_manager.close_browser_session()
                return

            success_count = 0
            uploaded_count = 0
            skipped_count = 0

            for i, message in enumerate(messages, 1):
                print(f"\n📄 Processing email {i}/{len(messages)}...")

                email_msg = self.email_handler.get_message_content(message['id'])
                if not email_msg:
                    continue

                email_data = self.email_handler.extract_email_data(email_msg)
                if not email_data:
                    continue

                print(f"📧 Subject: {email_data['subject']}")

                # Skip already-processed emails (unless forcing)
                if not force_reprocess and self.tracking_manager.is_email_processed(email_data):
                    info = self.tracking_manager.get_processed_info(email_data) or {}
                    print(f"⏭️  SKIPPED - already processed on {info.get('processed_date', 'unknown date')}")
                    skipped_count += 1
                    continue

                read_online_url = self.email_handler.extract_read_online_url(email_data)
                email_data['read_online_url'] = read_online_url
                if not read_online_url:
                    print("❌ No Read Online URL found, skipping...")
                    continue

                print(f"🔗 Found Read Online URL: {read_online_url}")

                # Cross-pipeline dedup: skip if the website pipeline already did this URL
                if not force_reprocess and self.is_url_already_processed_by_web(read_online_url):
                    print("⏭️  SKIPPED - URL already processed by web scanner (cross-dedup)")
                    skipped_count += 1
                    continue

                filename = str(create_safe_pdf_filename(
                    email_data['subject'], index=i, output_dir=output_path, prefix='dispatch'
                ))

                # Convert using the shared, image-safe conversion path — with
                # link following when enabled, matching the website pipeline
                # (fresh LinkProcessor per article to avoid shared state).
                if FOLLOW_ARTICLE_LINKS:
                    link_processor = LinkProcessor(self.browser_manager)
                    success = await link_processor.process_article_with_links(
                        read_online_url, filename, page=page
                    )
                else:
                    success = await self.browser_manager.convert_url_to_pdf_with_page(
                        read_online_url, filename, page
                    )
                if not success:
                    print("❌ Failed to convert")
                    continue

                success_count += 1
                print(f"✅ Successfully converted: {filename}")

                # Upload through the dedup gate (skips if already on the device)
                remarkable_uploaded = False
                if upload_to_remarkable:
                    if self.remarkable_manager.upload_if_new(filename, email_data['subject']):
                        uploaded_count += 1
                        remarkable_uploaded = True
                    else:
                        print(f"⚠️ Failed to upload {filename} to ReMarkable")

                if self.tracking_manager.mark_email_processed(
                    email_data, filename, remarkable_uploaded, success=True
                ):
                    self.tracking_manager.save_tracking_data()

                await asyncio.sleep(1)

            print("\n🎉 Email conversion complete!")
            print(f"✅ Successfully converted: {success_count}/{len(messages)} emails")
            print(f"⏭️  Skipped (already processed): {skipped_count}/{len(messages)} emails")
            print(f"📁 Check the '{output_dir}' directory for PDFs")
            if upload_to_remarkable:
                print(f"📤 Uploaded to ReMarkable: {uploaded_count}/{success_count} PDFs")

            self.tracking_manager.print_tracking_summary()

        finally:
            await self.browser_manager.close_browser_session()


async def run_email_converter():
    """Entry point for calling from main.py or other external callers."""
    converter = DispatchPersistentConverter(rmapi_path=DEFAULT_RMAPI_PATH)
    await converter.process_emails(
        output_dir='dispatch_persistent_pdfs',
        max_emails=DEFAULT_MAX_EMAILS,
        upload_to_remarkable=DEFAULT_UPLOAD_TO_REMARKABLE,
        force_reprocess=DEFAULT_FORCE_REPROCESS,
    )


async def main():
    try:
        await run_email_converter()
    except Exception as e:
        print(f"❌ ERROR: {e}")
        print(f"🔧 DEBUG: {traceback.format_exc()}")


if __name__ == "__main__":
    print("🚀 THE DISPATCH EMAIL CONVERTER + REMARKABLE UPLOAD")
    print("=" * 65)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
