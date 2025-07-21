#!/usr/bin/env python3
"""
Email handler for Gmail operations and email processing
"""

import base64
from bs4 import BeautifulSoup
import html2text

from config.settings import GMAIL_SEARCH_QUERY, DEFAULT_MAX_EMAILS


class EmailHandler:
    """Handles Gmail operations and email content processing"""
    
    def __init__(self, auth_manager):
        self.auth_manager = auth_manager
        self.service = None
        self.h2t = html2text.HTML2Text()
        self.h2t.ignore_links = False
        self.h2t.ignore_images = False
    
    def _get_service(self):
        """Get Gmail service instance"""
        if not self.service:
            self.service = self.auth_manager.get_gmail_service()
        return self.service
    
    def search_dispatch_emails(self, max_results=None):
        """Search for emails from The Dispatch"""
        if max_results is None:
            max_results = DEFAULT_MAX_EMAILS
            
        try:
            service = self._get_service()
            if not service:
                print("❌ Gmail service not available")
                return []
                
            results = service.users().messages().list(
                userId='me', q=GMAIL_SEARCH_QUERY, maxResults=max_results
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
            service = self._get_service()
            if not service:
                return None
                
            message = service.users().messages().get(
                userId='me', id=message_id, format='full'
            ).execute()
            return message
        except Exception as e:
            print(f"❌ Error getting message {message_id}: {e}")
            return None

    def _extract_body(self, payload):
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
                    body = self._extract_body(part)
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

            raw_body = self._extract_body(message['payload'])
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

    def process_email_list(self, messages):
        """Process a list of email messages and extract data"""
        processed_emails = []
        
        for i, message in enumerate(messages, 1):
            print(f"📄 Processing email {i}/{len(messages)}...")
            
            # Get email content
            email_msg = self.get_message_content(message['id'])
            if not email_msg:
                continue

            # Extract email data
            email_data = self.extract_email_data(email_msg)
            if not email_data:
                continue

            # Get Read Online URL
            read_online_url = self.extract_read_online_url(email_data)
            email_data['read_online_url'] = read_online_url
            
            processed_emails.append(email_data)
            
        return processed_emails

    def get_email_summary(self, email_data):
        """Get a summary string for an email"""
        subject = email_data.get('subject', 'No Subject')
        date = email_data.get('date', 'Unknown Date')
        return f"{subject} ({date})"
