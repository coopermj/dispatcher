#!/usr/bin/env python3
"""
The Dispatch Email to PDF Converter - Modular Version
Main entry point for the application
"""

import asyncio
import time
import traceback
from pathlib import Path

# Import modules
from modules import (
    AuthManager, EmailHandler, BrowserManager,
    TrackingManager, ReMarkableManager
)
from modules.utils import (
    setup_logging, create_safe_pdf_filename,
    create_summary_report, check_dependencies,
    format_file_size, get_file_info
)
from config.settings import (
    OUTPUT_DIR, DEFAULT_MAX_EMAILS, DEFAULT_FORCE_REPROCESS,
    DEFAULT_UPLOAD_TO_REMARKABLE, SLEEP_BETWEEN_CONVERSIONS,
    DEFAULT_RMAPI_PATH
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

        # Configuration
        self.output_dir = Path(output_dir or OUTPUT_DIR)
        self.output_dir.mkdir(exist_ok=True)

        # Statistics
        self.stats = {
            'total_emails': 0,
            'successful_conversions': 0,
            'skipped_duplicates': 0,
            'failed_conversions': 0,
            'remarkable_uploads': 0,
            'remarkable_failures': 0,
            'remarkable_enabled': False,
            'processing_time': 0,
            'total_file_size': 0
        }

    def print_startup_banner(self):
        """Print application startup banner"""
        print("🚀 THE DISPATCH PDF CONVERTER - MODULAR VERSION")
        print("📊 Enhanced with duplicate tracking and prevention")
        print("⚙️  Configuration loaded from .env file")
        print("=" * 65)
        print("Features:")
        print("• Modular architecture with separate components")
        print("• .env file configuration support")
        print("• Tracks processed emails to prevent duplicates")
        print("• Removes headers and saves authentication cookies")
        print("• Keeps the browser open throughout the entire process")
        print("• Uploads PDFs to ReMarkable News folder using rmapi")
        print("• Shows tracking summary and recently processed emails")
        print("\n📋 Processing steps:")
        print("1. Load configuration from .env file")
        print("2. Load tracking data from previous runs")
        print("3. Show summary of previously processed emails")
        print("4. Check rmapi availability (if enabled)")
        print("5. Authenticate with Google Gmail API")
        print("6. Start browser and authenticate with The Dispatch")
        print("7. Search for emails from The Dispatch")
        print("8. Skip emails that were already processed")
        print("9. Convert newsletter URLs to PDFs")
        print("10. Upload PDFs to ReMarkable (if enabled)")
        print("11. Update tracking database")
        print("12. Generate summary report")
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

        # Authenticate with Google
        if not self.auth_manager.authenticate_google():
            print("❌ Google authentication failed")
            return False

        # Start browser session
        if not await self.browser_manager.start_browser_session():
            print("❌ Browser session failed to start")
            return False

        # Authenticate with The Dispatch
        page = self.browser_manager.get_page()
        context = self.browser_manager.get_context()

        if not await self.auth_manager.authenticate_with_dispatch(page, context):
            print("❌ The Dispatch authentication failed")
            await self.browser_manager.close_browser_session()
            return False

        print("✅ All components initialized successfully")
        return True

    async def process_single_email(self, email_data, index):
        """Process a single email and convert to PDF"""
        try:
            print(f"\n📧 Processing: {email_data['subject']}")

            # Check if already processed
            if self.tracking_manager.is_email_processed(email_data):
                processed_info = self.tracking_manager.get_processed_info(email_data)
                print(f"⏭️  SKIPPED - Already processed on {processed_info.get('processed_date', 'unknown date')}")
                print(f"📁 Existing PDF: {processed_info.get('pdf_path', 'unknown path')}")
                print(
                    f"📤 ReMarkable: {'✅ Uploaded' if processed_info.get('remarkable_uploaded') else '❌ Not uploaded'}")
                self.stats['skipped_duplicates'] += 1
                return True

            # Check for Read Online URL
            read_online_url = email_data.get('read_online_url')
            if not read_online_url:
                print("❌ No Read Online URL found, skipping...")
                self.stats['failed_conversions'] += 1
                return False

            print(f"🔗 Found Read Online URL: {read_online_url}")

            # Create PDF filename
            pdf_filename = create_safe_pdf_filename(
                email_data['subject'],
                index=index,
                output_dir=self.output_dir
            )

            # Convert URL to PDF
            success = await self.browser_manager.convert_url_to_pdf(
                read_online_url,
                str(pdf_filename)
            )

            if not success:
                print(f"❌ Failed to convert to PDF")
                self.stats['failed_conversions'] += 1
                return False

            # Update file size stats
            file_info = get_file_info(pdf_filename)
            if file_info and 'size' in file_info:
                self.stats['total_file_size'] += file_info['size']
                print(f"📄 PDF size: {file_info['size_formatted']}")

            # Upload to ReMarkable if enabled
            remarkable_uploaded = False
            if self.stats['remarkable_enabled'] and self.remarkable_manager.is_available():
                upload_success = self.remarkable_manager.upload_pdf(pdf_filename)
                if upload_success:
                    self.stats['remarkable_uploads'] += 1
                    remarkable_uploaded = True
                else:
                    self.stats['remarkable_failures'] += 1
                    print(f"⚠️ Failed to upload to ReMarkable")

            # Mark as processed in tracking (only for successful conversions)
            tracking_success = self.tracking_manager.mark_email_processed(
                email_data,
                str(pdf_filename),
                remarkable_uploaded,
                success=True  # Only mark as processed if conversion was successful
            )

            if tracking_success:
                self.tracking_manager.save_tracking_data()

            print(f"✅ Successfully processed: {pdf_filename.name}")
            self.stats['successful_conversions'] += 1

            return True

        except Exception as e:
            print(f"❌ Error processing email: {e}")
            self.stats['failed_conversions'] += 1
            return False

    async def process_emails(self, max_emails=None, force_reprocess=None, upload_to_remarkable=None):
        """Main email processing function"""
        # Set defaults
        max_emails = max_emails or DEFAULT_MAX_EMAILS
        force_reprocess = force_reprocess if force_reprocess is not None else DEFAULT_FORCE_REPROCESS
        upload_to_remarkable = upload_to_remarkable if upload_to_remarkable is not None else DEFAULT_UPLOAD_TO_REMARKABLE

        # Update stats
        self.stats['remarkable_enabled'] = upload_to_remarkable

        start_time = time.time()

        try:
            # Initialize all components
            if not await self.initialize():
                return False

            # Search for emails
            print(f"\n🔍 Searching for up to {max_emails} emails from The Dispatch...")
            messages = self.email_handler.search_dispatch_emails(max_emails)

            if not messages:
                print("❌ No emails found")
                return False

            self.stats['total_emails'] = len(messages)

            # Process email list to extract data
            print(f"\n📧 Processing {len(messages)} emails...")
            email_list = self.email_handler.process_email_list(messages)

            # Filter emails if not force reprocessing
            if not force_reprocess:
                original_count = len(email_list)
                email_list = [
                    email for email in email_list
                    if not self.tracking_manager.is_email_processed(email)
                ]
                skipped_count = original_count - len(email_list)
                if skipped_count > 0:
                    print(f"⏭️  {skipped_count} emails already processed (use force_reprocess=True to override)")

            if not email_list:
                print("✅ All emails have been processed already!")
                return True

            print(f"\n🔄 Converting {len(email_list)} emails to PDF...")

            # Process each email
            for i, email_data in enumerate(email_list, 1):
                await self.process_single_email(email_data, i)

                # Small delay between conversions
                if i < len(email_list):
                    await asyncio.sleep(SLEEP_BETWEEN_CONVERSIONS)

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

    def print_final_summary(self):
        """Print final processing summary"""
        print(f"\n🎉 Processing complete!")

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

    async def cleanup(self):
        """Cleanup resources"""
        await self.browser_manager.close_browser_session()


async def main():
    """Main function"""
    try:
        # Create converter instance
        converter = DispatchConverter()

        # Print startup banner
        converter.print_startup_banner()

        # Process emails with default settings
        await converter.process_emails(
            max_emails=5,  # Number of emails to process
            force_reprocess=False,  # Set to True to reprocess already converted emails
            upload_to_remarkable=True  # Set to False to disable ReMarkable upload
        )

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
    print("   📧 max_emails: Set MAX_EMAILS in .env or modify main()")
    print("   🔄 force_reprocess: Set FORCE_REPROCESS=true in .env")
    print("   📱 upload_to_remarkable: Set UPLOAD_TO_REMARKABLE=false to disable")
    print("\n🔧 Environment setup:")
    print("   1. Copy .env.example to .env and customize")
    print("   2. Download Google OAuth credentials as 'credentials.json'")
    print("   3. Install and authenticate rmapi (for ReMarkable upload)")
    print("   4. Install dependencies: pip install -r requirements.txt")
    print("   5. Install Playwright browsers: playwright install")
    print("\n💡 Configuration options in .env:")
    print("   MAX_EMAILS=10                    # Number of emails to process")
    print("   FORCE_REPROCESS=true             # Reprocess already converted emails")
    print("   UPLOAD_TO_REMARKABLE=false       # Disable ReMarkable upload")
    print("   BROWSER_HEADLESS=true            # Run browser in background")
    print("   OUTPUT_DIR=my_pdfs               # Custom output directory")
    print("   RMAPI_PATH=/custom/path/rmapi    # Custom rmapi path")
    print("\nPress ENTER to start...")
    # input()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Goodbye!")
    except Exception as e:
        print(f"❌ CRITICAL ERROR: {e}")