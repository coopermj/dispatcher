#!/usr/bin/env python3
"""Is there a Dispatch email we have not converted yet?

The cheap probe behind the weekday-morning watcher (`run_pipeline.sh
--if-new-mail`, cron every 10 min 07:00–09:50). Gmail API only — no browser,
a couple of seconds — so the full run happens when a late Morning Dispatch
actually arrives instead of at a guessed time.

Exit status: 0 = new mail (run the pipeline), 1 = nothing new, 2 = could not
check (auth or search failed; the fixed slots will alert on a dead token).
"""
import sys

from modules import AuthManager, EmailHandler, TrackingManager
from config.settings import DISPATCH_EMAIL_TRACKING_FILE, DEFAULT_MAX_EMAILS


def find_unprocessed(email_handler, tracking_manager, max_results=None):
    """Emails from the search window that are not yet tracked as processed and
    carry a Read Online link (login emails and digests have none). None if the
    search itself failed."""
    messages = email_handler.search_dispatch_emails(max_results or DEFAULT_MAX_EMAILS)
    if messages is None:
        return None
    new = []
    for message in messages:
        content = email_handler.get_message_content(message['id'])
        if not content:
            continue
        email_data = email_handler.extract_email_data(content)
        if not email_data or tracking_manager.is_email_processed(email_data):
            continue
        if not email_handler.extract_read_online_url(email_data):
            continue
        new.append(email_data)
    return new


def main():
    auth = AuthManager()
    if not auth.authenticate_google():
        print("❌ Google authentication failed; cannot check mail")
        return 2
    handler = EmailHandler(auth)
    tracking = TrackingManager(tracking_file=DISPATCH_EMAIL_TRACKING_FILE)
    new = find_unprocessed(handler, tracking)
    if new is None:
        print("❌ Gmail search failed; cannot check mail")
        return 2
    if not new:
        print("ℹ️ No new Dispatch email")
        return 1
    for email_data in new:
        print(f"📧 New: {email_data.get('subject', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
