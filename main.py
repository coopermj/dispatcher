#!/usr/bin/env python3
"""
The Dispatch Email to PDF Converter - Modular Version
Main entry point for the application
"""

import argparse
import asyncio
import hashlib
import json
import time
import traceback
from datetime import datetime
from pathlib import Path

# Import modules
from modules import (
    AuthManager, EmailHandler, BrowserManager, 
    TrackingManager, ReMarkableManager, WebsiteScanner, LinkProcessor
)
from modules.utils import (
    setup_logging, create_safe_pdf_filename, 
    create_summary_report, check_dependencies,
    format_file_size, get_file_info
)
from config.settings import (
    OUTPUT_DIR, DEFAULT_MAX_EMAILS, DEFAULT_FORCE_REPROCESS,
    DEFAULT_UPLOAD_TO_REMARKABLE, SLEEP_BETWEEN_CONVERSIONS,
    DEFAULT_RMAPI_PATH, PROCESSING_MODE, MAX_ARTICLES, FOLLOW_ARTICLE_LINKS,
    MAX_CONCURRENT_CONVERSIONS
)


class DispatchConverter:
    """Main application class that orchestrates all modules"""
    
    def __init__(self, rmapi_path=None, output_dir=None):
        # Initialize all managers
        self.auth_manager = AuthManager()
        self.email_handler = EmailHandler(self.auth_manager)
        self.browser_manager = BrowserManager()
        self.tracking_manager = TrackingManager()
        self.remarkable_manager = ReMarkableManager(rmapi_path or DEFAULT_RMAPI_PATH)
        self.website_scanner = WebsiteScanner(self.browser_manager, self.tracking_manager)  # Pass tracking for early duplicate detection
        # Configuration
        self.output_dir = Path(output_dir or OUTPUT_DIR)
        self.output_dir.mkdir(exist_ok=True)
        self.processing_mode = PROCESSING_MODE
        
        # Statistics
        self.stats = {
            'total_emails': 0,
            'total_articles': 0,
            'successful_conversions': 0,
            'skipped_duplicates': 0,
            'failed_conversions': 0,
            'remarkable_uploads': 0,
            'remarkable_failures': 0,
            'remarkable_enabled': False,
            'processing_time': 0,
            'total_file_size': 0,
            'processing_mode': self.processing_mode,
            'total_linked_pages': 0,
            'follow_links_enabled': FOLLOW_ARTICLE_LINKS
        }
    
    def print_startup_banner(self):
        """Print application startup banner"""
        print("🚀 THE DISPATCH PDF CONVERTER - MODULAR VERSION")
        print("📊 Enhanced with duplicate tracking and prevention")
        print("⚙️  Configuration loaded from .env file")
        print(f"🔄 Processing mode: {self.processing_mode.upper()}")
        print("=" * 65)
        print("Features:")
        print("• Modular architecture with separate components")
        print("• .env file configuration support")
        print("• Dual mode: Email processing OR website scanning")
        print("• Enhanced PDF generation with linked page inclusion")
        print("• Tracks processed content to prevent duplicates")
        print("• Removes headers and saves authentication cookies")
        print("• Keeps the browser open throughout the entire process")
        print("• Uploads PDFs to ReMarkable News folder using rmapi")
        print("• Shows tracking summary and recently processed content")
        
        if FOLLOW_ARTICLE_LINKS:
            print("• 🔗 Link following enabled: Creates multi-page PDFs with referenced content")
        
        if self.processing_mode == 'email':
            print("\n📧 EMAIL MODE - Processing steps:")
            print("1. Load configuration from .env file")
            print("2. Load tracking data from previous runs")
            print("3. Authenticate with Google Gmail API")
            print("4. Search for emails from The Dispatch")
            print("5. Skip emails that were already processed")
            if FOLLOW_ARTICLE_LINKS:
                print("6. Convert newsletter URLs to PDFs (with linked content)")
            else:
                print("6. Convert newsletter URLs to PDFs")
        else:
            print("\n🌐 WEBSITE MODE - Processing steps:")
            print("1. Load configuration from .env file")
            print("2. Load tracking data from previous runs")
            print("3. Scan thedispatch.com for articles")
            print("4. Filter articles by age and keywords")
            print("5. Skip articles that were already processed")
            if FOLLOW_ARTICLE_LINKS:
                print("6. Convert article URLs to PDFs (with linked content)")
            else:
                print("6. Convert article URLs to PDFs")
        
        print("7. Upload PDFs to ReMarkable (if enabled)")
        print("8. Update tracking database")
        print("9. Generate summary report")

        print("\n📁 Files created:")
        print(f"- PDFs in: {self.output_dir}")
        print("- HTML snapshots in: debug_html/")
        print("- Cookies saved in: dispatch_cookies.json")
        print("- Tracking database: dispatch_tracking.json")
        print("\n📱 ReMarkable integration:")
        print(f"- rmapi path: {self.remarkable_manager.rmapi_path}")
        print(f"- Available: {'✅ Yes' if self.remarkable_manager.is_available() else '❌ No'}")
        print("=" * 65)

    async def initialize(self):
        """Initialize all components"""
        print("\n🔧 Initializing components...")
        
        # Check dependencies
        if not check_dependencies():
            print("❌ Please install missing dependencies before continuing")
            return False
        
        # Show tracking summary
        self.tracking_manager.print_tracking_summary()
        
        # Clean up tracking data
        self.tracking_manager.cleanup_tracking_data(self.output_dir)
        
        # For email mode, authenticate with Google
        if self.processing_mode == 'email':
            if not self.auth_manager.authenticate_google():
                print("❌ Google authentication failed")
                return False
        
        # Start browser session
        if not await self.browser_manager.start_browser_session():
            print("❌ Browser session failed to start")
            return False
        
        # Authenticate with The Dispatch (required for both modes)
        page = self.browser_manager.get_page()
        context = self.browser_manager.get_context()
        
        if not await self.auth_manager.authenticate_with_dispatch(page, context):
            print("❌ The Dispatch authentication failed")
            await self.browser_manager.close_browser_session()
            return False
        
        print("✅ All components initialized successfully")
        return True

    async def process_content(self, max_items=None, force_reprocess=None, upload_to_remarkable=None):
        """Main content processing function (emails or website articles)"""
        # Set defaults based on processing mode
        if self.processing_mode == 'email':
            max_items = max_items or DEFAULT_MAX_EMAILS
        else:
            max_items = max_items or MAX_ARTICLES
            
        force_reprocess = force_reprocess if force_reprocess is not None else DEFAULT_FORCE_REPROCESS
        upload_to_remarkable = upload_to_remarkable if upload_to_remarkable is not None else DEFAULT_UPLOAD_TO_REMARKABLE
        
        # Update stats
        self.stats['remarkable_enabled'] = upload_to_remarkable
        
        start_time = time.time()
        
        try:
            # Initialize all components
            if not await self.initialize():
                return False
            
            # Get content to process based on mode
            if self.processing_mode == 'email':
                content_list = await self.get_email_content(max_items)
            else:
                content_list = await self.get_website_content(max_items)
            
            if not content_list:
                print(f"❌ No {self.processing_mode} content found")
                return False
            
            # Filter content if not force reprocessing
            if not force_reprocess:
                original_count = len(content_list)
                content_list = [
                    content for content in content_list 
                    if not self.tracking_manager.is_email_processed(content)
                ]
                skipped_count = original_count - len(content_list)
                if skipped_count > 0:
                    print(f"⏭️  {skipped_count} items already processed (use force_reprocess=True to override)")
            
            if not content_list:
                print("✅ All content has been processed already!")
                return True
            
            print(f"\n🔄 Converting {len(content_list)} items to PDF...")
            print(f"⚡ Using parallel processing with up to {MAX_CONCURRENT_CONVERSIONS} concurrent conversions")

            # Process items in parallel with concurrency limit
            await self.process_items_parallel(content_list)
            
            # Calculate processing time
            self.stats['processing_time'] = time.time() - start_time
            
            # Print final summary
            self.print_final_summary()
            
            return True
            
        except Exception as e:
            print(f"❌ Error during processing: {e}")
            print(f"🔧 Debug info: {traceback.format_exc()}")
            return False
        
        finally:
            # Always close browser session
            await self.browser_manager.close_browser_session()

    async def get_email_content(self, max_emails):
        """Get email content to process"""
        print(f"\n🔍 Searching for up to {max_emails} emails from The Dispatch...")
        messages = self.email_handler.search_dispatch_emails(max_emails)
        
        if not messages:
            return []
        
        self.stats['total_emails'] = len(messages)
        
        # Process email list to extract data
        print(f"\n📧 Processing {len(messages)} emails...")
        return self.email_handler.process_email_list(messages)

    async def get_website_content(self, max_articles):
        """Get website content to process"""
        print(f"\n🔍 Scanning website for up to {max_articles} articles...")
        
        articles = await self.website_scanner.scan_for_articles(max_articles)
        self.stats['total_articles'] = len(articles)
        
        if articles:
            self.website_scanner.print_articles_summary()
        
        # Convert articles to format compatible with email processing
        content_list = []
        for article in articles:
            content_data = self.website_scanner.create_article_data_for_processing(article)
            content_list.append(content_data)

        return content_list

    async def process_items_parallel(self, content_list):
        """Process multiple items in parallel with concurrency limit"""
        # Create a semaphore to limit concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_CONVERSIONS)

        async def process_with_semaphore(content_data, index):
            async with semaphore:
                return await self.process_single_item_parallel(content_data, index)

        # Create tasks for all items
        tasks = [process_with_semaphore(content_data, i+1) for i, content_data in enumerate(content_list)]

        # Execute in parallel and gather results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Log any exceptions
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"❌ Error processing item {i+1}: {result}")

    async def process_single_item_parallel(self, content_data, index, force_reprocess: bool = False, effective_mode: str = None):
        """Process a single item using a dedicated browser page (for parallel operations)"""
        page = None
        try:
            mode = effective_mode or self.processing_mode
            item_type = "email" if mode == 'email' else "article"
            print(f"\n📄 [{index}] Processing {item_type}: {content_data['subject']}")

            # Check if already processed (skip check when force_reprocess=True)
            if not force_reprocess and self.tracking_manager.is_email_processed(content_data):
                processed_info = self.tracking_manager.get_processed_info(content_data)
                print(f"⏭️  [{index}] SKIPPED - Already processed on {processed_info.get('processed_date', 'unknown date')}")
                self.stats['skipped_duplicates'] += 1
                return True

            # Check for content URL
            content_url = content_data.get('read_online_url')
            if not content_url:
                print(f"❌ [{index}] No URL found for {item_type}, skipping...")
                self.stats['failed_conversions'] += 1
                return False

            print(f"🔗 [{index}] Found URL: {content_url}")

            # Create PDF filename
            pdf_filename = create_safe_pdf_filename(
                content_data['subject'],
                index=index,
                output_dir=self.output_dir,
                prefix=f"dispatch_{mode}"
            )

            # Create a new page for this conversion
            page = await self.browser_manager.create_new_page()

            # Convert URL to PDF
            if FOLLOW_ARTICLE_LINKS and mode == 'website':
                # Create a fresh LinkProcessor per article to avoid shared state in parallel runs
                link_processor = LinkProcessor(self.browser_manager)
                success = await link_processor.process_article_with_links(
                    content_url,
                    str(pdf_filename),
                    page=page
                )
                link_summary = link_processor.get_processing_summary()
                self.stats['total_linked_pages'] += link_summary.get('linked_pages', 0)
                if link_summary.get('linked_pages', 0) > 0:
                    print(f"📄 [{index}] Created multi-page PDF with {link_summary['total_pages']} pages")
            else:
                # Use parallel-safe PDF conversion
                if page:
                    success = await self.browser_manager.convert_url_to_pdf_with_page(
                        content_url,
                        str(pdf_filename),
                        page
                    )
                else:
                    success = await self.browser_manager.convert_url_to_pdf(
                        content_url,
                        str(pdf_filename)
                    )

            if not success:
                print(f"❌ [{index}] Failed to convert to PDF")
                self.stats['failed_conversions'] += 1
                return False

            # Update file size stats
            file_info = get_file_info(pdf_filename)
            if file_info and 'size' in file_info:
                self.stats['total_file_size'] += file_info['size']
                print(f"📄 [{index}] PDF size: {file_info['size_formatted']}")

            # Upload to ReMarkable if enabled
            remarkable_uploaded = False
            if self.stats['remarkable_enabled'] and self.remarkable_manager.is_available():
                upload_success = self.remarkable_manager.upload_pdf(pdf_filename)
                if upload_success:
                    self.stats['remarkable_uploads'] += 1
                    remarkable_uploaded = True
                else:
                    self.stats['remarkable_failures'] += 1
                    print(f"⚠️ [{index}] Failed to upload to ReMarkable")

            # Mark as processed in tracking
            tracking_success = self.tracking_manager.mark_email_processed(
                content_data,
                str(pdf_filename),
                remarkable_uploaded,
                success=True
            )

            if tracking_success:
                self.tracking_manager.save_tracking_data()

            print(f"✅ [{index}] Successfully processed: {pdf_filename.name}")
            self.stats['successful_conversions'] += 1

            return True

        except Exception as e:
            item_type = "email" if (effective_mode or self.processing_mode) == 'email' else "article"
            print(f"❌ [{index}] Error processing {item_type}: {e}")
            self.stats['failed_conversions'] += 1
            return False
        finally:
            # Clean up the page
            if page:
                await self.browser_manager.close_page(page)

    # Keep the old method name for backwards compatibility
    async def process_emails(self, max_emails=None, force_reprocess=None, upload_to_remarkable=None):
        """Legacy method for email processing (backwards compatibility)"""
        if self.processing_mode != 'email':
            print("⚠️ process_emails() called but processing mode is not 'email'")
            print(f"Current mode: {self.processing_mode}")
            
        return await self.process_content(max_emails, force_reprocess, upload_to_remarkable)

    def print_final_summary(self):
        """Print final processing summary"""
        mode_name = "Email" if self.processing_mode == 'email' else "Website"
        print(f"\n🎉 {mode_name} processing complete!")
        
        # Generate and print summary report
        report = create_summary_report(self.stats)
        print(report)
        
        # Show tracking summary
        self.tracking_manager.print_tracking_summary()
        
        # ReMarkable status
        if self.stats['remarkable_enabled']:
            self.remarkable_manager.print_status()
        
        # Recommendations
        print("\n💡 Next steps:")
        if self.stats['successful_conversions'] > 0:
            print(f"📁 Check '{self.output_dir}' for your PDF files")
        
        if self.stats['remarkable_uploads'] > 0:
            print("📱 Check your ReMarkable's 'News' folder for uploaded files")
        
        if self.stats['failed_conversions'] > 0:
            print("🔧 Check debug_html/ folder for snapshots of failed conversions")

    async def process_single_url(self, url):
        """Process a single URL directly, bypassing scanning. Always force-reprocesses."""
        from urllib.parse import urlparse
        # Derive a human-readable title from the URL slug
        path = urlparse(url).path
        segments = [s for s in path.split('/') if s]
        slug = segments[-1] if segments else 'article'
        subject = slug.replace('-', ' ').title()

        content_data = {
            'subject': subject,
            'read_online_url': url,
            'message_id': f"website_{hashlib.md5(url.encode()).hexdigest()}",
            'sender': 'CLI',
            'date': datetime.now().isoformat(),
            'body': '',
            'raw_body': f"<a href='{url}'>{subject}</a>",
            'is_html': True,
            'source': 'cli',
        }

        print(f"\n🔗 Processing URL: {url}")
        print(f"📄 Derived title: {subject}")
        await self.process_single_item_parallel(
            content_data, 1,
            force_reprocess=True,
            effective_mode='website'
        )

    def retry_failed_uploads(self):
        """Upload PDFs that were converted but never made it to reMarkable."""
        if not self.remarkable_manager.is_available():
            print("❌ ReMarkable not available — cannot retry uploads")
            return

        retried = 0
        succeeded = 0

        project_root = Path(__file__).parent

        # --- Modular tracking file (dispatch_tracking.json) ---
        for fingerprint, entry in list(self.tracking_manager.processed_emails.items()):
            if entry.get('remarkable_uploaded'):
                continue
            if entry.get('remarkable_expired'):
                continue
            pdf_path = Path(entry.get('pdf_path', ''))
            if not pdf_path.is_absolute():
                pdf_path = project_root / pdf_path
            if not pdf_path.exists():
                continue
            print(f"\n📤 Retrying upload: {entry.get('subject', '')[:60]}")
            retried += 1
            if self.remarkable_manager.upload_pdf(pdf_path):
                self.tracking_manager.processed_emails[fingerprint]['remarkable_uploaded'] = True
                succeeded += 1
        if retried:
            self.tracking_manager.save_tracking_data()

        # --- Email converter tracking file (dispatch_email_tracking.json) ---
        email_tracking_path = Path(__file__).parent / 'dispatch_email_tracking.json'
        if email_tracking_path.exists():
            with open(email_tracking_path, 'r') as f:
                email_tracking = json.load(f)

            email_retried = 0
            for fingerprint, entry in email_tracking.items():
                if entry.get('remarkable_uploaded'):
                    continue
                if entry.get('remarkable_expired'):
                    continue
                pdf_path = Path(entry.get('pdf_path', ''))
                if not pdf_path.is_absolute():
                    pdf_path = project_root / pdf_path
                if not pdf_path.exists():
                    continue
                print(f"\n📤 Retrying email upload: {entry.get('subject', '')[:60]}")
                email_retried += 1
                retried += 1
                if self.remarkable_manager.upload_pdf(pdf_path):
                    entry['remarkable_uploaded'] = True
                    succeeded += 1

            if email_retried:
                with open(email_tracking_path, 'w') as f:
                    json.dump(email_tracking, f, indent=2)

        print(f"\n✅ Retry complete: {succeeded}/{retried} uploaded successfully")

    async def cleanup(self):
        """Cleanup resources"""
        await self.browser_manager.close_browser_session()


async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description='The Dispatch PDF Converter')
    parser.add_argument('--url', type=str, default=None,
                        help='Convert a specific URL to PDF directly (skips scanning)')
    parser.add_argument('--skip-email', action='store_true',
                        help='Skip the email converter step')
    parser.add_argument('--retry-uploads', action='store_true',
                        help='Re-upload PDFs that were converted but never uploaded to reMarkable')
    args = parser.parse_args()

    try:
        # Create converter instance
        converter = DispatchConverter()

        # --retry-uploads mode: upload PDFs that failed to reach reMarkable
        if args.retry_uploads:
            converter.retry_failed_uploads()
            return

        # Print startup banner
        converter.print_startup_banner()

        # --url mode: process a single URL directly
        if args.url:
            if not await converter.initialize():
                return
            try:
                await converter.process_single_url(args.url)
                converter.print_final_summary()
            finally:
                await converter.cleanup()
        # Normal mode: scan and process (email or website based on PROCESSING_MODE in .env)
        else:
            await converter.process_content(
                force_reprocess=False,
                upload_to_remarkable=True
            )

        # Run email converter unless explicitly skipped
        if not args.skip_email:
            print("\n" + "=" * 65)
            print("📧 Starting email converter...")
            print("=" * 65)
            try:
                from email_converter import run_email_converter
                await run_email_converter()
            except Exception as e:
                print(f"⚠️ Email converter failed (website processing already complete): {e}")
                print(f"🔧 DEBUG: {traceback.format_exc()}")

    except KeyboardInterrupt:
        print("\n👋 Process interrupted by user")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
        print(f"🔧 DEBUG: {traceback.format_exc()}")


if __name__ == "__main__":
    # Print welcome message
    print("🚀 THE DISPATCH PDF CONVERTER - MODULAR VERSION")
    print("=" * 65)
    print("⚙️  Configuration:")
    print("   📁 .env file: Customize settings in .env file")
    print("   🔄 PROCESSING_MODE: Set to 'email' or 'website' in .env")
    print("   📧 Email mode: Process emails from Gmail")
    print("   🌐 Website mode: Scan thedispatch.com for articles")
    print("\n🔧 Environment setup:")
    print("   1. Copy .env.example to .env and customize")
    print("   2. Set PROCESSING_MODE=email or PROCESSING_MODE=website")
    print("   3. For email mode: Download Google OAuth credentials")
    print("   4. Install dependencies: pip install -r requirements.txt")
    print("   5. Install Playwright browsers: playwright install")
    print("   6. Setup rmapi for ReMarkable (optional)")
    print("\n💡 Configuration options in .env:")
    print("   PROCESSING_MODE=website          # Switch to website scanning")
    print("   MAX_EMAILS=10                    # Number of emails (email mode)")
    print("   MAX_ARTICLES=15                  # Number of articles (website mode)")
    print("   FORCE_REPROCESS=true             # Reprocess already converted content")
    print("   UPLOAD_TO_REMARKABLE=false       # Disable ReMarkable upload")
    print("   WEBSITE_SECTIONS=newsletters,morning-dispatch  # Sections to scan")
    print("   SKIP_KEYWORDS=podcast,video      # Keywords to skip in titles")
    print("   FOLLOW_ARTICLE_LINKS=true        # Include linked pages in PDFs")
    print("\nPress ENTER to start...")
    # input()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")
