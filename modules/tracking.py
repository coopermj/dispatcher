#!/usr/bin/env python3
"""
Tracking manager for duplicate detection and processing history
"""

import json
import hashlib
import os
from datetime import datetime
from pathlib import Path

from config.settings import TRACKING_FILE


class TrackingManager:
    """Manages email processing tracking and duplicate detection"""

    def __init__(self):
        self.processed_emails = {}
        self.load_tracking_data()

    def load_tracking_data(self):
        """Load previously processed email tracking data"""
        try:
            if TRACKING_FILE.exists():
                with open(TRACKING_FILE, 'r') as f:
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
            with open(TRACKING_FILE, 'w') as f:
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

    def is_url_processed(self, url):
        """Check if a URL has been successfully processed before (for early duplicate detection)"""
        # Check all tracked items for matching URL
        for fingerprint, data in self.processed_emails.items():
            if not data.get('success', False):
                continue

            # Check if the URL matches the read_online_url or is in the message_id
            stored_url = data.get('read_online_url', '')
            message_id = data.get('message_id', '')

            # Direct URL match
            if stored_url and stored_url == url:
                # Verify PDF still exists
                pdf_path = data.get('pdf_path', '')
                if pdf_path and os.path.exists(pdf_path):
                    return True

            # Check by URL hash (website articles use this pattern)
            url_hash = f"website_{hash(url)}"
            if message_id == url_hash:
                pdf_path = data.get('pdf_path', '')
                if pdf_path and os.path.exists(pdf_path):
                    return True

        return False

    def get_processed_urls(self):
        """Get a set of all successfully processed URLs for fast lookup"""
        processed_urls = set()

        for fingerprint, data in self.processed_emails.items():
            if not data.get('success', False):
                continue

            # Check if PDF still exists
            pdf_path = data.get('pdf_path', '')
            if not pdf_path or not os.path.exists(pdf_path):
                continue

            # Add the read_online_url if present
            url = data.get('read_online_url', '')
            if url:
                processed_urls.add(url)

        return processed_urls

    def get_processed_subjects(self):
        """Get a set of all successfully processed subjects/titles for fast lookup"""
        processed_subjects = set()

        for fingerprint, data in self.processed_emails.items():
            if not data.get('success', False):
                continue

            # Check if PDF still exists
            pdf_path = data.get('pdf_path', '')
            if not pdf_path or not os.path.exists(pdf_path):
                continue

            # Add the subject if present
            subject = data.get('subject', '')
            if subject:
                processed_subjects.add(subject.lower().strip())

        return processed_subjects

    def is_email_processed(self, email_data):
        """Check if an email has been successfully processed before"""
        fingerprint = self.get_email_fingerprint(email_data)
        processed_info = self.processed_emails.get(fingerprint)

        if not processed_info:
            return False

        # Only consider it processed if it was successful
        if not processed_info.get('success', False):
            return False

        # Verify the PDF still exists
        pdf_path = processed_info.get('pdf_path', '')
        if pdf_path and os.path.exists(pdf_path):
            return True
        else:
            # PDF no longer exists, remove from tracking
            print(f"🧹 Removing tracking for missing PDF: {pdf_path}")
            del self.processed_emails[fingerprint]
            self.save_tracking_data()
            return False

    def mark_email_processed(self, email_data, pdf_path, remarkable_uploaded=False, success=True):
        """Mark an email as processed and store metadata (only if successful)"""
        if not success:
            print("⚠️ Not tracking failed conversion")
            return False

        # Verify PDF was actually created and is valid
        if not os.path.exists(pdf_path):
            print(f"⚠️ PDF file not found, not tracking: {pdf_path}")
            return False

        # Check minimum file size
        try:
            file_size = os.path.getsize(pdf_path)
            if file_size < 5000:  # Minimum size for valid PDF
                print(f"⚠️ PDF file too small ({file_size} bytes), not tracking: {pdf_path}")
                return False
        except OSError:
            print(f"⚠️ Cannot access PDF file, not tracking: {pdf_path}")
            return False

        fingerprint = self.get_email_fingerprint(email_data)

        self.processed_emails[fingerprint] = {
            'subject': email_data.get('subject', ''),
            'sender': email_data.get('sender', ''),
            'date': email_data.get('date', ''),
            'message_id': email_data.get('message_id', ''),
            'read_online_url': email_data.get('read_online_url', ''),  # Store URL for duplicate detection
            'processed_date': datetime.now().isoformat(),
            'pdf_path': str(pdf_path),
            'pdf_size': file_size,
            'remarkable_uploaded': remarkable_uploaded,
            'fingerprint': fingerprint,
            'success': True  # Only successful conversions are tracked
        }

        print(f"✅ Email marked as successfully processed in tracking")
        return True

    def get_processed_info(self, email_data):
        """Get processing info for an email if it exists"""
        fingerprint = self.get_email_fingerprint(email_data)
        return self.processed_emails.get(fingerprint)

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

    def cleanup_tracking_data(self, output_dir=None):
        """Remove tracking entries for PDFs that no longer exist or failed conversions"""
        cleaned_count = 0
        to_remove = []

        for fingerprint, data in self.processed_emails.items():
            should_remove = False

            # Remove if not marked as successful
            if not data.get('success', False):
                print(f"🧹 Removing failed conversion: {data.get('subject', 'Unknown')[:50]}...")
                should_remove = True
            else:
                # Remove if PDF no longer exists
                pdf_path = data.get('pdf_path', '')
                if pdf_path and not os.path.exists(pdf_path):
                    print(f"🧹 Removing missing PDF: {pdf_path}")
                    should_remove = True

            if should_remove:
                to_remove.append(fingerprint)
                cleaned_count += 1

        for fingerprint in to_remove:
            del self.processed_emails[fingerprint]

        if cleaned_count > 0:
            print(f"🧹 Cleaned up {cleaned_count} invalid tracking entries")
            self.save_tracking_data()

        return cleaned_count

    def get_processing_stats(self):
        """Get statistics about successfully processed emails"""
        if not self.processed_emails:
            return {
                'total': 0,
                'uploaded_to_remarkable': 0,
                'failed_uploads': 0,
                'total_size': 0
            }

        # Only count successful conversions
        successful_emails = [
            email for email in self.processed_emails.values()
            if email.get('success', False)
        ]

        total = len(successful_emails)
        uploaded = sum(1 for email in successful_emails
                       if email.get('remarkable_uploaded', False))
        failed_uploads = total - uploaded
        total_size = sum(email.get('pdf_size', 0) for email in successful_emails)

        return {
            'total': total,
            'uploaded_to_remarkable': uploaded,
            'failed_uploads': failed_uploads,
            'total_size': total_size
        }

    def print_tracking_summary(self):
        """Print a summary of tracking status"""
        print("\n📊 TRACKING SUMMARY")
        print("=" * 50)

        stats = self.get_processing_stats()
        print(f"📝 Successfully processed emails: {stats['total']}")
        print(f"📤 Uploaded to ReMarkable: {stats['uploaded_to_remarkable']}")
        print(f"❌ Failed uploads: {stats['failed_uploads']}")

        if stats['total_size'] > 0:
            size_mb = stats['total_size'] / (1024 * 1024)
            print(f"📁 Total PDF size: {size_mb:.1f} MB")

        if stats['total'] > 0:
            print("\n📋 Recently processed emails:")
            recent = self.list_processed_emails(5)
            for i, email in enumerate(recent, 1):
                size_info = ""
                if email.get('pdf_size'):
                    size_kb = email['pdf_size'] / 1024
                    size_info = f" ({size_kb:.1f} KB)"

                print(f"  {i}. {email.get('subject', 'No Subject')[:50]}...{size_info}")
                print(f"     📅 {email.get('processed_date', 'Unknown date')}")
                print(f"     📤 ReMarkable: {'✅' if email.get('remarkable_uploaded') else '❌'}")
        else:
            print("\n📋 No successfully processed emails yet")
        print("=" * 50)

    def export_tracking_data(self, export_path):
        """Export tracking data to a file"""
        try:
            with open(export_path, 'w') as f:
                json.dump(self.processed_emails, f, indent=2)
            print(f"📤 Exported tracking data to: {export_path}")
            return True
        except Exception as e:
            print(f"❌ Error exporting tracking data: {e}")
            return False

    def import_tracking_data(self, import_path):
        """Import tracking data from a file"""
        try:
            with open(import_path, 'r') as f:
                imported_data = json.load(f)

            # Merge with existing data
            original_count = len(self.processed_emails)
            self.processed_emails.update(imported_data)
            new_count = len(self.processed_emails)

            print(f"📥 Imported tracking data from: {import_path}")
            print(f"📊 Added {new_count - original_count} new entries")

            self.save_tracking_data()
            return True
        except Exception as e:
            print(f"❌ Error importing tracking data: {e}")
            return False

    def reset_tracking_data(self):
        """Reset all tracking data (use with caution)"""
        self.processed_emails = {}
        self.save_tracking_data()
        print("🗑️ All tracking data has been reset")

    def update_remarkable_status(self, email_data, uploaded=True):
        """Update the ReMarkable upload status for a processed email"""
        fingerprint = self.get_email_fingerprint(email_data)
        if fingerprint in self.processed_emails:
            self.processed_emails[fingerprint]['remarkable_uploaded'] = uploaded
            self.save_tracking_data()
            print(f"📤 Updated ReMarkable status for: {email_data.get('subject', 'Unknown')[:50]}...")
            return True
        return False