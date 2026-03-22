#!/usr/bin/env python3
"""
Configuration settings for the Dispatch PDF Converter
Loads settings from .env file if available, with fallbacks to defaults
"""

import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables from .env file
BASE_DIR = Path(__file__).parent.parent
ENV_FILE = BASE_DIR / ".env"

# Load .env file if it exists
if ENV_FILE.exists():
    load_dotenv(ENV_FILE)
    print(f"✅ Loaded configuration from {ENV_FILE}")
else:
    print(f"📝 No .env file found at {ENV_FILE}, using defaults and environment variables")


# Helper function to get boolean from environment
def get_bool_env(key, default=False):
    """Get boolean value from environment variable"""
    value = os.getenv(key, str(default)).lower()
    return value in ('true', '1', 'yes', 'on')


# Helper function to get int from environment
def get_int_env(key, default=0):
    """Get integer value from environment variable"""
    try:
        return int(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


# Helper function to get float from environment
def get_float_env(key, default=0.0):
    """Get float value from environment variable"""
    try:
        return float(os.getenv(key, default))
    except (ValueError, TypeError):
        return default


# Base directories
CONFIG_DIR = BASE_DIR / "config"
OUTPUT_DIR = Path(os.getenv('OUTPUT_DIR', 'dispatch_pdfs'))
DEBUG_DIR = Path(os.getenv('DEBUG_DIR', 'debug_html'))

# Authentication files
CREDENTIALS_FILE = Path(os.getenv('CREDENTIALS_FILE', 'credentials.json'))
TOKEN_FILE = Path(os.getenv('TOKEN_FILE', 'token.pickle'))
COOKIES_FILE = Path(os.getenv('COOKIES_FILE', 'dispatch_cookies.json'))
TRACKING_FILE = Path(os.getenv('TRACKING_FILE', 'dispatch_tracking.json'))

# Make paths absolute if they're relative
if not CREDENTIALS_FILE.is_absolute():
    CREDENTIALS_FILE = BASE_DIR / CREDENTIALS_FILE
if not TOKEN_FILE.is_absolute():
    TOKEN_FILE = BASE_DIR / TOKEN_FILE
if not COOKIES_FILE.is_absolute():
    COOKIES_FILE = BASE_DIR / COOKIES_FILE
if not TRACKING_FILE.is_absolute():
    TRACKING_FILE = BASE_DIR / TRACKING_FILE
if not OUTPUT_DIR.is_absolute():
    OUTPUT_DIR = BASE_DIR / OUTPUT_DIR
if not DEBUG_DIR.is_absolute():
    DEBUG_DIR = BASE_DIR / DEBUG_DIR

# Google OAuth scopes
GOOGLE_SCOPES = [
    'https://www.googleapis.com/auth/gmail.readonly',
    'https://www.googleapis.com/auth/userinfo.email',
    'https://www.googleapis.com/auth/userinfo.profile',
    'openid'
]

# Processing mode settings
PROCESSING_MODE = os.getenv('PROCESSING_MODE', 'website').lower()  # 'email' or 'website'

# Website scanning settings
MAX_ARTICLES = get_int_env('MAX_ARTICLES', 10)
ARTICLE_AGE_LIMIT_DAYS = get_int_env('ARTICLE_AGE_LIMIT_DAYS', 30)
WEBSITE_SECTIONS = [s.strip() for s in
                    os.getenv('WEBSITE_SECTIONS', 'newsletters,morning-dispatch,afternoon-dispatch').split(',')]
SKIP_KEYWORDS = [k.strip().lower() for k in os.getenv('SKIP_KEYWORDS', 'podcast,video,live-stream').split(',')]

# Enhanced PDF generation settings
FOLLOW_ARTICLE_LINKS = get_bool_env('FOLLOW_ARTICLE_LINKS', False)
MAX_LINKED_PAGES = get_int_env('MAX_LINKED_PAGES', 10)
LINK_FOLLOW_DEPTH = get_int_env('LINK_FOLLOW_DEPTH', 1)
ALLOWED_LINK_DOMAINS = [d.strip() for d in os.getenv('ALLOWED_LINK_DOMAINS', '').split(',') if d.strip()]
SKIP_LINK_PATTERNS = [p.strip() for p in os.getenv('SKIP_LINK_PATTERNS',
                                                   'twitter.com,facebook.com,youtube.com,instagram.com,linkedin.com,mailto:').split(
    ',') if p.strip()]
LINKED_PAGE_TIMEOUT = get_int_env('LINKED_PAGE_TIMEOUT', 15)
REPLACE_LINKS_WITH_PDF_REFS = get_bool_env('REPLACE_LINKS_WITH_PDF_REFS', True)

# Parallel processing settings
MAX_CONCURRENT_SCANS = get_int_env('MAX_CONCURRENT_SCANS', 3)
MAX_CONCURRENT_CONVERSIONS = get_int_env('MAX_CONCURRENT_CONVERSIONS', 3)
MAX_CONCURRENT_LINKS = get_int_env('MAX_CONCURRENT_LINKS', 3)

# Skip domains file - domains to skip when following links
SKIP_DOMAINS_FILE = Path(os.getenv('SKIP_DOMAINS_FILE', 'skip_domains.txt'))
if not SKIP_DOMAINS_FILE.is_absolute():
    SKIP_DOMAINS_FILE = BASE_DIR / SKIP_DOMAINS_FILE


def load_skip_domains():
    """Load skip domains from file"""
    domains = []
    if SKIP_DOMAINS_FILE.exists():
        try:
            with open(SKIP_DOMAINS_FILE, 'r') as f:
                for line in f:
                    line = line.strip()
                    # Skip empty lines and comments
                    if line and not line.startswith('#'):
                        domains.append(line.lower())
            print(f"✅ Loaded {len(domains)} skip domains from {SKIP_DOMAINS_FILE}")
        except Exception as e:
            print(f"⚠️ Error loading skip domains: {e}")
    else:
        print(f"📝 No skip domains file found at {SKIP_DOMAINS_FILE}")
    return domains


SKIP_DOMAINS = load_skip_domains()

# Email search settings
GMAIL_SEARCH_QUERY = os.getenv('GMAIL_SEARCH_QUERY', 'from:@thedispatch.com')
DEFAULT_MAX_EMAILS = get_int_env('MAX_EMAILS', 5)

# Browser settings
BROWSER_USER_AGENT = os.getenv('BROWSER_USER_AGENT',
                               'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36')
BROWSER_HEADLESS = get_bool_env('BROWSER_HEADLESS', False)
BROWSER_TIMEOUT = get_int_env('BROWSER_TIMEOUT', 30000)

# PDF generation settings
PDF_FORMAT = os.getenv('PDF_FORMAT', 'A4')
PDF_MARGINS = {
    'top': os.getenv('PDF_MARGIN_TOP', '0.75in'),
    'right': os.getenv('PDF_MARGIN_RIGHT', '0.75in'),
    'bottom': os.getenv('PDF_MARGIN_BOTTOM', '0.75in'),
    'left': os.getenv('PDF_MARGIN_LEFT', '0.75in')
}

# ReMarkable settings
DEFAULT_RMAPI_PATH = os.getenv('RMAPI_PATH', "~/rmapi/rmapi")
REMARKABLE_FOLDER = os.getenv('REMARKABLE_FOLDER', "News")
RMAPI_TIMEOUT = get_int_env('RMAPI_TIMEOUT', 60)

# The Dispatch website settings
DISPATCH_BASE_URL = os.getenv('DISPATCH_BASE_URL', "https://thedispatch.com")
LOGIN_INDICATORS = [
    'sign in',
    'login',
    'log in',
    'subscribe',
    'get started'
]
LOGGED_IN_INDICATORS = [
    'account',
    'subscription',
    'profile',
    'logout',
    'sign out',
    'settings',
    'subscriber',
    'my account'
]

# Content selectors for waiting
CONTENT_SELECTORS = [
    'article',
    '.article',
    '.post',
    '.content',
    'main'
]

# Elements to remove before PDF generation
HEADER_REMOVAL_KEYWORDS = [
    'navbar',
    'banner',
    'paywall',
    'newsletter',
    'comment',
    'breadcrumb',
    'subscribe',
    'sidebar',
    'popup',
    'ad',
    'site-footer',
    'site-info',
    'z-scroll-to',
    'primary-button'
]

# Logging settings
LOG_LEVEL = os.getenv('LOG_LEVEL', "INFO")
LOG_FILE = os.getenv('LOG_FILE', '')  # Empty string means no file logging
DATE_FORMAT = os.getenv('DATE_FORMAT', "%Y-%m-%d %H:%M:%S")

# Processing settings
DEFAULT_FORCE_REPROCESS = get_bool_env('FORCE_REPROCESS', False)
DEFAULT_UPLOAD_TO_REMARKABLE = get_bool_env('UPLOAD_TO_REMARKABLE', True)
SLEEP_BETWEEN_CONVERSIONS = get_float_env('SLEEP_BETWEEN_CONVERSIONS', 1.0)
CONTENT_LOAD_WAIT = get_float_env('CONTENT_LOAD_WAIT', 3.0)

# File size limits
MIN_PDF_SIZE_BYTES = get_int_env('MIN_PDF_SIZE_BYTES', 5000)

# Advanced settings
DEBUG_MODE = get_bool_env('DEBUG_MODE', False)
CLEANUP_DAYS = get_int_env('CLEANUP_DAYS', 30)
MAX_FILENAME_LENGTH = get_int_env('MAX_FILENAME_LENGTH', 100)


# Create directories if they don't exist
def ensure_directories():
    """Create necessary directories if they don't exist"""
    directories = [OUTPUT_DIR, DEBUG_DIR, CONFIG_DIR]
    for directory in directories:
        directory.mkdir(exist_ok=True)


def print_configuration_summary():
    """Print a summary of current configuration"""
    print("\n⚙️  CONFIGURATION SUMMARY")
    print("=" * 50)
    print(f"📧 Max emails: {DEFAULT_MAX_EMAILS}")
    print(f"🔄 Force reprocess: {DEFAULT_FORCE_REPROCESS}")
    print(f"🌐 Browser headless: {BROWSER_HEADLESS}")
    print(f"📱 ReMarkable upload: {DEFAULT_UPLOAD_TO_REMARKABLE}")
    print(f"📁 Output directory: {OUTPUT_DIR}")
    print(f"🔧 rmapi path: {DEFAULT_RMAPI_PATH}")
    print(f"📊 Debug mode: {DEBUG_MODE}")
    print("=" * 50)


def validate_configuration():
    """Validate configuration settings and warn about issues"""
    issues = []

    # Check required files
    if not CREDENTIALS_FILE.exists():
        issues.append(f"❌ Google credentials file not found: {CREDENTIALS_FILE}")

    # Check rmapi path if ReMarkable is enabled
    if DEFAULT_UPLOAD_TO_REMARKABLE:
        rmapi_path = Path(DEFAULT_RMAPI_PATH).expanduser()
        if not rmapi_path.exists():
            issues.append(f"⚠️ rmapi not found at: {rmapi_path} (ReMarkable upload may fail)")

    # Validate numeric settings
    if DEFAULT_MAX_EMAILS <= 0:
        issues.append(f"⚠️ MAX_EMAILS should be positive, got: {DEFAULT_MAX_EMAILS}")

    if BROWSER_TIMEOUT <= 0:
        issues.append(f"⚠️ BROWSER_TIMEOUT should be positive, got: {BROWSER_TIMEOUT}")

    # Print issues if any
    if issues:
        print("\n⚠️  CONFIGURATION ISSUES")
        print("=" * 50)
        for issue in issues:
            print(issue)
        print("=" * 50)
        return False
    else:
        print("✅ Configuration validation passed")
        return True


def get_env_file_template():
    """Get the .env file template content"""
    return """# Copy from .env.example and customize your settings
MAX_EMAILS=5
FORCE_REPROCESS=false
BROWSER_HEADLESS=false
UPLOAD_TO_REMARKABLE=true
RMAPI_PATH=~/rmapi/rmapi
OUTPUT_DIR=dispatch_pdfs
"""


def create_env_file_if_missing():
    """Create a basic .env file if it doesn't exist"""
    if not ENV_FILE.exists():
        try:
            with open(ENV_FILE, 'w') as f:
                f.write(get_env_file_template())
            print(f"📝 Created basic .env file at {ENV_FILE}")
            print("💡 Customize the settings in .env file as needed")
        except Exception as e:
            print(f"⚠️ Could not create .env file: {e}")


# Initialize settings
ensure_directories()

# Create .env file if missing (optional)
# create_env_file_if_missing()

# Print configuration if in debug mode
if DEBUG_MODE:
    print_configuration_summary()

# Validate configuration
validate_configuration()