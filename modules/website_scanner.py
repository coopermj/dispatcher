#!/usr/bin/env python3
"""
Website scanner for The Dispatch articles
Scans thedispatch.com for articles to convert to PDF
"""

import asyncio
import re
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup

from config.settings import (
    DISPATCH_BASE_URL, MAX_ARTICLES, ARTICLE_AGE_LIMIT_DAYS,
    WEBSITE_SECTIONS, SKIP_KEYWORDS, BROWSER_TIMEOUT
)


class WebsiteScanner:
    """Scans The Dispatch website for articles to convert"""

    def __init__(self, browser_manager):
        self.browser_manager = browser_manager
        self.found_articles = []

    async def scan_for_articles(self, max_articles=None):
        """Scan The Dispatch website for articles"""
        max_articles = max_articles or MAX_ARTICLES

        print(f"🌐 Scanning {DISPATCH_BASE_URL} for articles...")
        print(f"📊 Looking for up to {max_articles} articles in sections: {', '.join(WEBSITE_SECTIONS)}")

        all_articles = []

        # Scan main homepage first
        homepage_articles = await self.scan_homepage()
        all_articles.extend(homepage_articles)

        # Scan specific sections
        for section in WEBSITE_SECTIONS:
            if len(all_articles) >= max_articles:
                break
            section_articles = await self.scan_section(section)
            all_articles.extend(section_articles)

        # Remove duplicates based on URL
        seen_urls = set()
        unique_articles = []
        for article in all_articles:
            if article['url'] not in seen_urls:
                seen_urls.add(article['url'])
                unique_articles.append(article)

        # Filter by age and keywords
        filtered_articles = self.filter_articles(unique_articles)

        # Limit to max_articles
        self.found_articles = filtered_articles[:max_articles]

        print(f"✅ Found {len(self.found_articles)} articles to process")
        return self.found_articles

    async def scan_homepage(self):
        """Scan the homepage for articles"""
        try:
            print(f"🔍 Scanning homepage...")
            page = self.browser_manager.get_page()

            await page.goto(DISPATCH_BASE_URL, timeout=BROWSER_TIMEOUT)
            await asyncio.sleep(3)

            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')

            articles = self.extract_articles_from_soup(soup, "homepage")
            print(f"📄 Found {len(articles)} articles on homepage")
            return articles

        except Exception as e:
            print(f"❌ Error scanning homepage: {e}")
            return []

    async def scan_section(self, section):
        """Scan a specific section for articles"""
        try:
            section_url = f"{DISPATCH_BASE_URL}/{section}"
            print(f"🔍 Scanning section: {section_url}")

            page = self.browser_manager.get_page()

            await page.goto(section_url, timeout=BROWSER_TIMEOUT)
            await asyncio.sleep(3)

            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')

            articles = self.extract_articles_from_soup(soup, section)
            print(f"📄 Found {len(articles)} articles in {section}")
            return articles

        except Exception as e:
            print(f"❌ Error scanning section {section}: {e}")
            return []

    def extract_articles_from_soup(self, soup, source):
        """Extract article information from BeautifulSoup object"""
        articles = []

        # Common selectors for articles on The Dispatch
        article_selectors = [
            'article',
            '.post',
            '.entry',
            '.article-item',
            '.story',
            'a[href*="/p/"]',  # Substack-style post URLs
            'a[href*="/article/"]',
            'a[href*="/newsletter/"]'
        ]

        for selector in article_selectors:
            elements = soup.select(selector)

            for element in elements:
                article_data = self.extract_article_data(element, source)
                if article_data:
                    articles.append(article_data)

        return articles

    def extract_article_data(self, element, source):
        """Extract article data from a single element"""
        try:
            # Try to find article URL
            url = None

            # If element is a link, use its href
            if element.name == 'a' and element.get('href'):
                url = element.get('href')
            else:
                # Look for links within the element
                link = element.find('a', href=True)
                if link:
                    url = link.get('href')

            if not url:
                return None

            # Make URL absolute
            if url.startswith('/'):
                url = urljoin(DISPATCH_BASE_URL, url)
            elif not url.startswith('http'):
                return None

            # Only process thedispatch.com URLs
            if 'thedispatch.com' not in url:
                return None

            # Extract title
            title = self.extract_title(element)
            if not title:
                return None

            # Extract date (try multiple methods)
            date = self.extract_date(element)

            # Extract summary/excerpt
            summary = self.extract_summary(element)

            return {
                'url': url,
                'title': title.strip(),
                'date': date,
                'summary': summary,
                'source': source,
                'extracted_at': datetime.now().isoformat()
            }

        except Exception as e:
            print(f"⚠️ Error extracting article data: {e}")
            return None

    def extract_title(self, element):
        """Extract article title from element"""
        # Try multiple approaches to find title
        title_selectors = [
            'h1', 'h2', 'h3', 'h4',
            '.title', '.headline', '.post-title',
            '.entry-title', '.article-title'
        ]

        for selector in title_selectors:
            title_elem = element.select_one(selector)
            if title_elem:
                return title_elem.get_text().strip()

        # If element is a link, use its text
        if element.name == 'a':
            return element.get_text().strip()

        # Look for any text content
        text = element.get_text().strip()
        if text and len(text) < 200:  # Reasonable title length
            return text

        return None

    def extract_date(self, element):
        """Extract article date from element"""
        # Look for time elements
        time_elem = element.find('time')
        if time_elem:
            datetime_attr = time_elem.get('datetime')
            if datetime_attr:
                try:
                    return datetime.fromisoformat(datetime_attr.replace('Z', '+00:00'))
                except:
                    pass

        # Look for date patterns in text
        date_selectors = [
            '.date', '.published', '.post-date',
            '.entry-date', '.article-date'
        ]

        for selector in date_selectors:
            date_elem = element.select_one(selector)
            if date_elem:
                date_text = date_elem.get_text().strip()
                parsed_date = self.parse_date_string(date_text)
                if parsed_date:
                    return parsed_date

        return None

    def parse_date_string(self, date_text):
        """Parse date string into datetime object"""
        try:
            # Common date formats
            formats = [
                '%Y-%m-%d',
                '%B %d, %Y',
                '%b %d, %Y',
                '%m/%d/%Y',
                '%d/%m/%Y'
            ]

            for fmt in formats:
                try:
                    return datetime.strptime(date_text, fmt)
                except ValueError:
                    continue

        except Exception:
            pass

        return None

    def extract_summary(self, element):
        """Extract article summary/excerpt"""
        summary_selectors = [
            '.excerpt', '.summary', '.description',
            '.post-excerpt', '.entry-summary'
        ]

        for selector in summary_selectors:
            summary_elem = element.select_one(selector)
            if summary_elem:
                return summary_elem.get_text().strip()[:200]

        return ""

    def filter_articles(self, articles):
        """Filter articles by age and keywords"""
        filtered = []
        cutoff_date = datetime.now() - timedelta(days=ARTICLE_AGE_LIMIT_DAYS)

        for article in articles:
            # Skip if title contains skip keywords
            title_lower = article['title'].lower()
            if any(keyword in title_lower for keyword in SKIP_KEYWORDS):
                print(f"⏭️  Skipping (keyword): {article['title'][:50]}...")
                continue

            # Skip if too old (if we have a date)
            article_date = article.get('date')
            if article_date and article_date < cutoff_date:
                print(f"⏭️  Skipping (too old): {article['title'][:50]}...")
                continue

            # Skip obvious non-article URLs
            url_lower = article['url'].lower()
            if any(skip in url_lower for skip in ['author', 'tag', 'category', 'search']):
                continue

            filtered.append(article)

        return filtered

    def get_found_articles(self):
        """Get the list of found articles"""
        return self.found_articles

    def create_article_data_for_processing(self, article):
        """Convert article data to format compatible with email processing"""
        return {
            'subject': article['title'],
            'sender': 'The Dispatch Website',
            'date': article.get('date', datetime.now()).isoformat() if article.get(
                'date') else datetime.now().isoformat(),
            'message_id': f"website_{hash(article['url'])}",
            'read_online_url': article['url'],
            'body': article.get('summary', ''),
            'raw_body': f"<a href='{article['url']}'>{article['title']}</a>",
            'is_html': True,
            'source': 'website_scan'
        }

    async def test_article_accessibility(self, article_url):
        """Test if an article URL is accessible"""
        try:
            page = self.browser_manager.get_page()

            response = await page.goto(article_url, timeout=BROWSER_TIMEOUT)

            if response.status == 200:
                # Check if we're not redirected to login page
                current_url = page.url
                if 'login' in current_url.lower() or 'signin' in current_url.lower():
                    return False, "Requires login"

                # Check for paywall indicators
                content = await page.content()
                if 'paywall' in content.lower() or 'subscribe' in content.lower():
                    return True, "May have paywall (but accessible)"

                return True, "Accessible"
            else:
                return False, f"HTTP {response.status}"

        except Exception as e:
            return False, str(e)

    def print_articles_summary(self):
        """Print summary of found articles"""
        if not self.found_articles:
            print("📄 No articles found")
            return

        print(f"\n📄 FOUND ARTICLES SUMMARY")
        print("=" * 50)
        print(f"Total articles found: {len(self.found_articles)}")

        for i, article in enumerate(self.found_articles[:10], 1):  # Show first 10
            date_str = ""
            if article.get('date'):
                date_str = f" ({article['date'].strftime('%Y-%m-%d')})"

            print(f"{i:2d}. {article['title'][:60]}...{date_str}")
            print(f"    🔗 {article['url']}")

        if len(self.found_articles) > 10:
            print(f"    ... and {len(self.found_articles) - 10} more articles")

        print("=" * 50)