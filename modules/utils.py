#!/usr/bin/env python3
"""
Utility functions and helpers for the Dispatch PDF Converter
"""

import re
import logging
from datetime import datetime
from pathlib import Path

from config.settings import LOG_LEVEL, DATE_FORMAT


def sanitize_filename(filename):
    """Create safe filename by removing invalid characters"""
    # Remove invalid characters for filenames
    filename = re.sub(r'[^\w\s-]', '', filename)
    # Replace multiple spaces/hyphens with single hyphen
    filename = re.sub(r'[-\s]+', '-', filename)
    # Limit length and remove leading/trailing hyphens
    filename = filename.strip('-')[:100]
    return filename


def create_timestamp(format_string=None):
    """Create timestamp string"""
    format_string = format_string or "%Y%m%d_%H%M%S"
    return datetime.now().strftime(format_string)


def create_safe_pdf_filename(subject, index=None, output_dir=".", prefix="dispatch"):
    """Create a safe PDF filename from email subject"""
    safe_subject = sanitize_filename(subject)

    if index is not None:
        filename = f"{prefix}_{index:03d}_{safe_subject}.pdf"
    else:
        timestamp = create_timestamp()
        filename = f"{prefix}_{timestamp}_{safe_subject}.pdf"

    return Path(output_dir) / filename


def setup_logging(level=None, log_file=None):
    """Setup logging configuration"""
    level = level or LOG_LEVEL

    # Convert string level to logging constant
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt=DATE_FORMAT
    )

    # Setup console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # Setup root logger
    logging.basicConfig(
        level=numeric_level,
        handlers=[console_handler]
    )

    # Add file handler if specified
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        logging.getLogger().addHandler(file_handler)

    return logging.getLogger(__name__)


def format_file_size(size_bytes):
    """Format file size in human readable format"""
    if size_bytes == 0:
        return "0 B"

    size_names = ["B", "KB", "MB", "GB"]
    i = 0
    while size_bytes >= 1024 and i < len(size_names) - 1:
        size_bytes /= 1024.0
        i += 1

    return f"{size_bytes:.1f} {size_names[i]}"


def print_progress_bar(current, total, prefix="Progress", suffix="Complete", length=50):
    """Print a progress bar to console"""
    percent = (current / total) * 100
    filled_length = int(length * current // total)
    bar = '█' * filled_length + '-' * (length - filled_length)
    print(f'\r{prefix} |{bar}| {current}/{total} ({percent:.1f}%) {suffix}', end='')

    if current == total:
        print()  # New line when complete


def validate_email_data(email_data):
    """Validate that email data contains required fields"""
    required_fields = ['subject', 'sender', 'date', 'message_id']

    if not email_data:
        return False, "Email data is None"

    missing_fields = []
    for field in required_fields:
        if not email_data.get(field):
            missing_fields.append(field)

    if missing_fields:
        return False, f"Missing required fields: {', '.join(missing_fields)}"

    return True, "Valid"


def truncate_string(text, max_length=50, suffix="..."):
    """Truncate string to specified length with suffix"""
    if not text:
        return ""

    if len(text) <= max_length:
        return text

    return text[:max_length - len(suffix)] + suffix


def parse_email_address(email_string):
    """Extract email address from string like 'Name <email@domain.com>'"""
    import re

    # Pattern to match email in angle brackets
    match = re.search(r'<([^>]+)>', email_string)
    if match:
        return match.group(1)

    # Pattern to match standalone email
    match = re.search(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', email_string)
    if match:
        return match.group(0)

    return email_string


def format_date_string(date_string):
    """Format date string for display"""
    try:
        # Try to parse common date formats
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(date_string)
        return dt.strftime("%Y-%m-%d %H:%M")
    except:
        # If parsing fails, return truncated original
        return truncate_string(date_string, 20)


def create_summary_report(stats):
    """Create a formatted summary report"""
    report = []
    report.append("=" * 60)
    report.append("📊 DISPATCH PDF CONVERTER - SUMMARY REPORT")
    report.append("=" * 60)

    # Basic stats
    report.append(f"📧 Total emails found: {stats.get('total_emails', 0)}")
    report.append(f"✅ Successfully converted: {stats.get('successful_conversions', 0)}")
    report.append(f"⏭️  Skipped (duplicates): {stats.get('skipped_duplicates', 0)}")
    report.append(f"❌ Failed conversions: {stats.get('failed_conversions', 0)}")

    # ReMarkable stats
    if stats.get('remarkable_enabled', False):
        report.append(f"📤 Uploaded to ReMarkable: {stats.get('remarkable_uploads', 0)}")
        report.append(f"📱 Upload failures: {stats.get('remarkable_failures', 0)}")

    # Time stats
    if stats.get('processing_time'):
        report.append(f"⏱️  Processing time: {stats['processing_time']:.1f} seconds")

    # File stats
    if stats.get('total_file_size'):
        report.append(f"📁 Total PDF size: {format_file_size(stats['total_file_size'])}")

    report.append("=" * 60)

    return "\n".join(report)


def check_dependencies():
    """Check if required dependencies are installed"""
    dependencies = {
        'google.auth': 'pip install google-auth google-auth-oauthlib google-auth-httplib2',
        'googleapiclient': 'pip install google-api-python-client',
        'bs4': 'pip install beautifulsoup4',
        'html2text': 'pip install html2text',
        'dotenv': 'pip install python-dotenv',
        'playwright': 'pip install playwright && playwright install'
    }

    missing = []

    for package, install_cmd in dependencies.items():
        try:
            __import__(package)
        except ImportError:
            missing.append((package, install_cmd))

    if missing:
        print("❌ Missing dependencies:")
        for package, install_cmd in missing:
            print(f"   {package}: {install_cmd}")
        print(f"\n💡 Quick install: pip install -r requirements.txt && playwright install")
        return False

    print("✅ All dependencies are installed")
    return True


def get_file_info(file_path):
    """Get basic file information"""
    try:
        path = Path(file_path)
        if not path.exists():
            return None

        stat = path.stat()
        return {
            'name': path.name,
            'size': stat.st_size,
            'size_formatted': format_file_size(stat.st_size),
            'modified': datetime.fromtimestamp(stat.st_mtime),
            'created': datetime.fromtimestamp(stat.st_ctime)
        }
    except Exception as e:
        return {'error': str(e)}


def cleanup_old_files(directory, days_old=30, pattern="*.pdf"):
    """Clean up old files in directory"""
    try:
        directory = Path(directory)
        if not directory.exists():
            return 0

        cutoff_date = datetime.now().timestamp() - (days_old * 24 * 60 * 60)
        cleaned_count = 0

        for file_path in directory.glob(pattern):
            if file_path.stat().st_mtime < cutoff_date:
                file_path.unlink()
                cleaned_count += 1
                print(f"🗑️ Removed old file: {file_path.name}")

        if cleaned_count > 0:
            print(f"🧹 Cleaned up {cleaned_count} old files from {directory}")

        return cleaned_count

    except Exception as e:
        print(f"❌ Error cleaning up files: {e}")
        return 0