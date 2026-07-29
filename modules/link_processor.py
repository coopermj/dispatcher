#!/usr/bin/env python3
"""
Link processor for following and including linked pages in PDFs
"""

import asyncio
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path

from modules.link_queue import LinkQueue, normalize_url
from modules.blocked_detection import detect_block, placeholder_html

from config.settings import (
    FOLLOW_ARTICLE_LINKS, MAX_LINKED_PAGES, LINK_FOLLOW_DEPTH,
    ALLOWED_LINK_DOMAINS, SKIP_LINK_PATTERNS, LINKED_PAGE_TIMEOUT,
    DEBUG_DIR, SKIP_DOMAINS, MAX_CONCURRENT_LINKS
)

# Sites like CNN keep <html>/<body> at viewport height with overflow:hidden and
# scroll an inner wrapper instead. Chromium's print layout then clips the
# document to a single viewport and repeats it on every printed page, producing
# a PDF of N identical pages. Restoring document-level flow before page.pdf()
# lets the full article paginate normally.
PRINT_FLOW_FIX_JS = """
() => {
    for (const el of [document.documentElement, document.body]) {
        if (!el) continue;
        el.style.setProperty('overflow', 'visible', 'important');
        el.style.setProperty('height', 'auto', 'important');
        el.style.setProperty('max-height', 'none', 'important');
    }
}
"""


class LinkProcessor:
    """Processes links within articles and creates multi-page PDFs"""

    def __init__(self, browser_manager):
        self.browser_manager = browser_manager
        self.processed_links = set()
        self.link_to_page_map = {}  # Maps URLs to PDF page numbers
        self._active_page = None  # Dedicated page for current processing
        self._owns_page = False   # Whether we created the page and should close it

    @staticmethod
    def _temp_dir_for(output_filename):
        """Temp dir for one conversion's intermediate PDFs.

        Keyed on the (unique) output filename so concurrent conversions each get
        their own directory. Previously every conversion shared
        debug_html/temp_pdfs/ with fixed names like page_1_main.pdf, so parallel
        runs clobbered each other's main-article PDF and the merged output ended
        up containing a different article's content.
        """
        return Path(DEBUG_DIR) / "temp_pdfs" / Path(output_filename).stem

    async def process_article_with_links(self, article_url, output_filename, page=None):
        """Process an article and all its linked pages into a single clean PDF (no headers)"""
        if not FOLLOW_ARTICLE_LINKS:
            # Fall back to browser manager's proven method
            if page:
                return await self.browser_manager.convert_url_to_pdf_with_page(article_url, output_filename, page)
            return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)

        print(f"🔗 Processing article with linked pages: {article_url}")

        # Set up dedicated page for this processing run
        if page:
            self._active_page = page
            self._owns_page = False
        else:
            self._active_page = await self.browser_manager.create_new_page()
            self._owns_page = True
            if not self._active_page:
                print("❌ Failed to create dedicated page, falling back to shared page")
                self._active_page = self.browser_manager.get_page()
                self._owns_page = False

        try:
            # Step 1: Check if we can merge PDFs first
            merge_available = await self.test_merge_availability()
            if not merge_available:
                print("📄 PDF merging not available, using standard single-page PDF generation")
                return await self.browser_manager.convert_url_to_pdf_with_page(article_url, output_filename, self._active_page)

            # Step 2: Load main article and extract links using browser manager navigation
            # Reset state for new article
            self.processed_links.clear()
            self.link_to_page_map.clear()

            # Navigate to main article using dedicated page (no headers added)
            if not await self.browser_manager.navigate_to_url_with_page(article_url, self._active_page):
                print("❌ Failed to load main article")
                return False

            # Wait for page to be fully stable before extracting content
            try:
                await self._active_page.wait_for_load_state('networkidle', timeout=10000)
            except:
                pass  # Continue even if timeout - page may still be usable
            await asyncio.sleep(1)  # Extra stability wait

            # Get page content and extract links
            content = await self._active_page.content()
            soup = BeautifulSoup(content, 'html.parser')
            links = self.extract_links(soup, article_url)

            # Set up a per-conversion temp directory for intermediate PDFs. Unique
            # per output filename so concurrent conversions can't clobber each
            # other's pages (the cause of cross-article content bleed).
            temp_dir = self._temp_dir_for(output_filename)
            temp_dir.mkdir(parents=True, exist_ok=True)
            pdf_pages = []

            # *** CRITICAL FIX: Generate main article PDF FIRST, while still on the page ***
            # This must happen BEFORE any link following to avoid browser state corruption
            main_pdf = temp_dir / "page_1_main.pdf"
            print(f"📄 Creating PDF for main article FIRST (before link following): {article_url}")

            # Save HTML before cleanup for debugging
            await self.browser_manager.save_html_snapshot_from_page(self._active_page, "before_cleanup", article_url)

            # Remove header elements before PDF generation
            await self.browser_manager.remove_header_elements_from_page(self._active_page)

            # Save HTML after cleanup for debugging
            await self.browser_manager.save_html_snapshot_from_page(self._active_page, "after_cleanup", article_url)

            # Force lazy-loaded images to load before generating PDF.
            # 1. Set loading="eager" to tell the browser to fetch immediately.
            # 2. Scroll to the bottom and back to trigger IntersectionObserver
            #    (required for images that use sizes="auto").
            await self._active_page.evaluate("""
                () => {
                    document.querySelectorAll('img[loading="lazy"]').forEach(img => {
                        img.loading = 'eager';
                        if (img.dataset.src) img.src = img.dataset.src;
                        if (img.dataset.srcset) img.srcset = img.dataset.srcset;
                    });
                    document.querySelectorAll('source[data-srcset]').forEach(s => {
                        s.srcset = s.dataset.srcset;
                    });
                }
            """)
            # Scroll to bottom to trigger IntersectionObserver for any remaining lazy elements
            await self._active_page.evaluate("""
                async () => {
                    window.scrollTo(0, document.body.scrollHeight);
                    await new Promise(r => setTimeout(r, 500));
                    window.scrollTo(0, 0);
                    await new Promise(r => setTimeout(r, 200));
                }
            """)
            try:
                await self._active_page.wait_for_load_state('networkidle', timeout=8000)
            except:
                pass

            # Generate PDF directly from current page state (don't re-navigate)
            print(f"📄 Generating PDF: {main_pdf}")
            await self._active_page.pdf(
                path=str(main_pdf),
                format='A4',
                margin={'top': '0.75in', 'right': '0.75in', 'bottom': '0.75in', 'left': '0.75in'},
                print_background=True,
                prefer_css_page_size=False
            )

            # Check if PDF was created successfully
            import os
            main_pdf_success = main_pdf.exists() and main_pdf.stat().st_size > 5000

            if main_pdf_success and main_pdf.exists():
                size = main_pdf.stat().st_size
                print(f"  ✅ Main PDF created: {main_pdf.name} ({size} bytes)")
                pdf_pages.append(str(main_pdf))
            else:
                print(f"  ❌ Failed to create main PDF - falling back to single page mode")
                return await self.browser_manager.convert_url_to_pdf_with_page(
                    article_url, output_filename, self._active_page
                )

            # If no links found, just use the main PDF
            if not links:
                print("📄 No relevant links found, using main article PDF only")
                import shutil
                shutil.move(str(main_pdf), output_filename)
                self.link_to_page_map = {article_url: 1}
                shutil.rmtree(temp_dir, ignore_errors=True)
                return True

            # Step 3: BFS-capture linked pages (extracts deeper links as it goes)
            linked_sections = await self.capture_linked_pages(links, article_url, temp_dir)

            if not linked_sections:
                print("📄 No accessible linked pages, using main article PDF only")
                import shutil
                shutil.move(str(main_pdf), output_filename)
                self.link_to_page_map = {article_url: 1}
                shutil.rmtree(temp_dir, ignore_errors=True)
                return True

            placeholders = sum(1 for s in linked_sections if s['blocked'])
            print(f"📄 Captured {len(linked_sections)} linked sections "
                  f"({placeholders} placeholders), merging...")

            # Step 4: Merge main + linked sections with nested bookmarks and
            # internal link rewriting
            sections = [{'url': article_url, 'title': 'Main Article',
                         'pdf': str(main_pdf), 'depth': 0,
                         'parent_url': None, 'blocked': None}]
            sections.extend(linked_sections)

            success = await self.merge_sections(sections, output_filename)

            if success and Path(output_filename).exists():
                final_size = Path(output_filename).stat().st_size
                print(f"✅ Final merged PDF: {final_size} bytes")
                # Keep the summary map for get_processing_summary()
                self.link_to_page_map = {s['url']: s['start_page'] + 1
                                         for s in sections if 'start_page' in s}
            else:
                print(f"❌ Merge failed, using main article only")
                import shutil
                shutil.copy(str(main_pdf), output_filename)
                self.link_to_page_map = {article_url: 1}
                success = True

            pdf_pages = [s['pdf'] for s in sections]

            # Cleanup temp files and this conversion's temp subdirectory
            for pdf_file in pdf_pages:
                try:
                    Path(pdf_file).unlink(missing_ok=True)
                except:
                    pass
            try:
                import shutil
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass

            if success:
                print(f"✅ Generated multi-page PDF with {len(pdf_pages)} pages")
                return True
            else:
                print("❌ Failed to create PDF")
                return False

        except Exception as e:
            print(f"❌ Error processing article with links: {e}")
            import traceback
            traceback.print_exc()
            # Always fall back to clean single-page conversion
            print("🔄 Falling back to clean single-page PDF conversion...")
            return await self.browser_manager.convert_url_to_pdf_with_page(article_url, output_filename, self._active_page)
        finally:
            # Clean up the dedicated page if we created it
            if self._owns_page and self._active_page:
                await self.browser_manager.close_page(self._active_page)
            self._active_page = None
            self._owns_page = False

    async def test_merge_availability(self):
        """Test if PDF merging tools are available"""
        try:
            # Test PyPDF2
            try:
                from PyPDF2 import PdfMerger
                return True
            except ImportError:
                pass
            
            # Test pdfunite
            try:
                import subprocess
                result = subprocess.run(['pdfunite', '--help'], 
                                      capture_output=True, timeout=5)
                if result.returncode == 0:
                    return True
            except:
                pass
            
            return False
            
        except Exception as e:
            return False
    
    # Class/id substrings marking regions whose links are NOT in-text citations:
    # related/recommended widgets, comment threads, navigation, footers, etc.
    # Following these turned single articles into 80-page multi-article PDFs.
    _EXCLUDED_REGION_RE = re.compile(
        r'related|recommend|more-from|morefrom|you-might|read-next|readnext|up-next|'
        r'suggested|popular|trending|most-read|mostread|comment|disqus|'
        r'sidebar|widget|shelf|digest|footer|nav|aside|promo|subscribe|newsletter',
        re.IGNORECASE,
    )

    def _in_excluded_region(self, link_element):
        """True if the link sits inside a related/recommended/comments/nav region
        rather than the article body (so it's not a genuine in-text citation)."""
        node = link_element
        for _ in range(12):  # walk a bounded number of ancestors
            node = getattr(node, 'parent', None)
            if node is None or getattr(node, 'name', None) is None:
                break
            if node.name in ('aside', 'footer', 'nav'):
                return True
            # CMS "post-type-*" classes (e.g. "post-type-newsletter") mark the
            # content-type of the whole article wrapper, not a promo/widget
            # region — drop them before matching so a bare "newsletter" or
            # "digest" substring doesn't disqualify an entire newsletter-format
            # article's body (e.g. Morning/Evening Dispatch, Boiling Frogs).
            classes = [c for c in (node.get('class', []) or []) if not c.startswith('post-type-')]
            tokens = ' '.join(classes) + ' ' + (node.get('id', '') or '')
            if tokens.strip() and self._EXCLUDED_REGION_RE.search(tokens):
                return True
        return False

    def extract_links(self, soup, base_url):
        """Extract relevant article links from a page"""
        all_links = []
        relevant_links = []
        
        # Focus on links within article content areas
        content_areas = soup.find_all(['article', 'main', '.content', '.post-content', '.entry-content'])
        if not content_areas:
            # Fallback to body if no specific content areas found
            content_areas = [soup.find('body')] if soup.find('body') else [soup]
        
        print(f"🔍 Searching for links in {len(content_areas)} content areas")
        
        for content_area in content_areas:
            if content_area is None:
                continue
                
            # Find all links within content areas
            area_links = content_area.find_all('a', href=True)
            print(f"📄 Found {len(area_links)} total links in content area")
            
            for link in area_links:
                href = link.get('href')
                if not href:
                    continue
                
                # Make URL absolute
                absolute_url = urljoin(base_url, href)
                all_links.append(absolute_url)
                
                # Skip if already processed
                if absolute_url in self.processed_links:
                    continue
                
                # Get link context to help determine if it's an article link
                link_text = link.get_text().strip()
                
                print(f"🔗 Checking link: {link_text[:50]}... -> {absolute_url}")
                
                # Skip links in related/recommended/comments/nav regions — only
                # genuine in-text citations should be followed.
                if self._in_excluded_region(link):
                    print(f"  ❌ In related/recommended/comments region")
                    continue

                # Apply URL-based filtering
                if not self.should_follow_link(absolute_url):
                    print(f"  ❌ Filtered out by URL rules")
                    continue
                
                # Additional context-based filtering
                if not self.is_likely_article_link(link, link_text, absolute_url):
                    print(f"  ❌ Filtered out by context rules")
                    continue
                
                print(f"  ✅ Keeping link: {link_text[:30]}...")
                
                relevant_links.append({
                    'url': absolute_url,
                    'text': link_text,
                    'original_href': href,
                    'context': self.get_link_context(link)
                })
                
                self.processed_links.add(absolute_url)
        
        print(f"📊 Link extraction summary:")
        print(f"  🔗 Total links found: {len(all_links)}")
        print(f"  ✅ Relevant links: {len(relevant_links)}")
        print(f"  📋 Sample URLs:")
        for i, link in enumerate(relevant_links[:3]):
            print(f"    {i+1}. {link['text'][:40]}... -> {link['url']}")
        
        return relevant_links
    
    def get_link_context(self, link_element):
        """Get context around a link to help determine its purpose"""
        try:
            # Get parent elements for context
            context_text = ""
            parent = link_element.parent
            if parent:
                context_text = parent.get_text().strip()[:100]
            
            # Check for surrounding text that indicates article content
            return context_text
        except:
            return ""
    
    def is_likely_article_link(self, link_element, link_text, url):
        """Additional checks to determine if a link is likely to an article"""
        try:
            # In-text citations in newsletter prose are often single words
            # ("says", "too"), so short anchors are legitimate — the URL has
            # already passed the article-URL rules by the time we're called.
            # Only drop empty or symbol-only anchors (icons, arrows).
            if len(link_text.strip()) < 2 or not any(c.isalnum() for c in link_text):
                return False
            
            # Skip links that are clearly navigation
            nav_texts = [
                'home', 'about', 'contact', 'subscribe', 'login', 'sign up', 'menu',
                'search', 'archive', 'categories', 'tags', 'next', 'previous',
                'more', 'all', 'view all', 'read more', 'continue reading'
            ]
            if link_text.lower().strip() in nav_texts:
                return False
            
            # Skip links with classes/IDs that suggest navigation
            link_classes = ' '.join(link_element.get('class', [])).lower()
            link_id = link_element.get('id', '').lower()
            
            nav_indicators = [
                'nav', 'menu', 'header', 'footer', 'sidebar', 'widget',
                'button', 'btn', 'social', 'share', 'tag', 'category'
            ]
            if any(indicator in link_classes or indicator in link_id for indicator in nav_indicators):
                return False
            
            # Prefer links that have substantive text (likely article titles)
            if len(link_text) > 20 and not any(word in link_text.lower() for word in ['click', 'here', 'more', 'continue']):
                return True
            
            # Check if link is in a context that suggests it's an article reference
            parent_element = link_element.parent
            if parent_element:
                parent_text = parent_element.get_text().lower()
                article_context_words = [
                    'read', 'article', 'story', 'report', 'analysis', 'see also',
                    'background', 'context'
                ]
                if any(word in parent_text for word in article_context_words):
                    return True
            
            # Default to following if it passed the URL-based checks
            return True
            
        except Exception as e:
            print(f"⚠️ Error checking link context: {e}")
            return True  # When in doubt, include it
    
    def should_follow_link(self, url):
        """Determine if a link should be followed (only article/content links)"""
        try:
            parsed = urlparse(url)
            
            print(f"  🔍 Analyzing URL: {url}")
            
            # Skip non-HTTP links
            if parsed.scheme not in ['http', 'https']:
                print(f"    ❌ Non-HTTP scheme: {parsed.scheme}")
                return False
            
            # Check domain restrictions
            if ALLOWED_LINK_DOMAINS:
                domain_match = any(domain in parsed.netloc for domain in ALLOWED_LINK_DOMAINS)
                if not domain_match:
                    print(f"    ❌ Domain not in allowed list: {parsed.netloc}")
                    return False

            # Check skip domains (from skip_domains.txt)
            if SKIP_DOMAINS:
                for skip_domain in SKIP_DOMAINS:
                    if skip_domain in parsed.netloc.lower():
                        print(f"    ❌ Domain in skip list: {skip_domain}")
                        return False
            
            # Skip patterns (social media, etc.)
            url_lower = url.lower()
            for pattern in SKIP_LINK_PATTERNS:
                if pattern in url_lower:
                    print(f"    ❌ Matches skip pattern: {pattern}")
                    return False
            
            # SPECIFIC EXCLUSIONS for The Dispatch
            dispatch_exclusions = [
                '/join/',
                '/join',
                '/subscribe',
                '/subscription',
                '/account',
                '/profile',
                '/settings',
                '/preferences',
                '/login',
                '/signup',
                '/register',
                '/auth',
                '/user/',
                '/billing',
                '/payment',
                '/checkout',
                '/cart',
                '/dashboard',
                '/admin',
                '/my-account',
                '/manage',
                '/plans',
                '/pricing',
                '/upgrade',
                '/membership'
            ]
            
            for exclusion in dispatch_exclusions:
                if exclusion in url_lower:
                    print(f"    ❌ Dispatch exclusion: {exclusion}")
                    return False
            
            # Skip common file types that won't render well
            skip_extensions = ['.pdf', '.doc', '.docx', '.zip', '.exe', '.dmg', '.mp4', '.mp3', '.jpg', '.png', '.gif']
            for ext in skip_extensions:
                if url_lower.endswith(ext):
                    print(f"    ❌ File extension: {ext}")
                    return False
            
            # Skip fragments (same page links)
            if parsed.fragment and not parsed.path:
                print(f"    ❌ Fragment-only link")
                return False
            
            # Skip obvious navigation/site structure pages
            navigation_patterns = [
                '/about', '/contact', '/privacy', '/terms', '/help', '/support',
                '/sitemap', '/search?', '/category/', '/tag/', '/archive/', '/author/',
                '/contributors', '/staff', '/team', '/careers', '/jobs', '/press',
                '/masthead', '/ethics', '/corrections', '/newsletters', '/podcasts'
            ]
            for pattern in navigation_patterns:
                if pattern in url_lower:
                    print(f"    ❌ Navigation pattern: {pattern}")
                    return False
            
            # FOR The Dispatch: Be permissive with article content
            if 'thedispatch.com' in parsed.netloc:
                # Additional The Dispatch specific exclusions
                dispatch_specific_exclusions = [
                    'thedispatch.com/join',
                    'thedispatch.com/account',
                    'thedispatch.com/subscribe',
                    'thedispatch.com/login',
                    'thedispatch.com/signup'
                ]
                
                for exclusion in dispatch_specific_exclusions:
                    if exclusion in url_lower:
                        print(f"    ❌ Specific Dispatch exclusion: {exclusion}")
                        return False
                
                print(f"    ✅ Dispatch content URL - allowing")
                return True
            
            # Check for typical article URL structures for other domains
            article_patterns = [
                '/p/', '/post/', '/article/', '/story/', '/news/', '/analysis/',
                '/newsletter/', '/dispatch/', '/morning-dispatch/', '/afternoon-dispatch/',
                '/evening-dispatch/', '/commentary/', '/opinion/', '/politics/',
                '/policy/', '/investigation/', '/report/', '/feature/'
            ]
            
            has_article_pattern = any(pattern in url_lower for pattern in article_patterns)
            if has_article_pattern:
                print(f"    ✅ Has article pattern")
                return True
            
            # Check for date patterns (e.g., /2024/01/ or /2024-01-15/)
            date_pattern = re.search(r'/20\d{2}[/-]\d{1,2}', url_lower)
            if date_pattern:
                print(f"    ✅ Has date pattern")
                return True
            
            # Check for typical article URL structures
            path_segments = [seg for seg in parsed.path.split('/') if seg]
            if path_segments:
                last_segment = path_segments[-1].lower()
                # Skip if it looks like a category or index page
                if last_segment in ['index', 'all', 'latest', 'recent', 'popular', 'trending']:
                    print(f"    ❌ Index/category page: {last_segment}")
                    return False
                
                # Article URLs often have descriptive titles with hyphens
                if len(last_segment) > 10 and '-' in last_segment:
                    print(f"    ✅ Descriptive URL segment")
                    return True
            
            # Skip very short paths that are likely navigation
            if len(parsed.path.strip('/')) < 3:
                print(f"    ❌ Very short path")
                return False
            
            print(f"    ⚠️ No clear article indicators - rejecting for safety")
            return False  # Changed from True to False for safety
            
        except Exception as e:
            print(f"⚠️ Error checking link {url}: {e}")
            return False
    
    async def capture_linked_pages(self, seed_links, main_url, temp_dir):
        """BFS-capture linked pages up to LINK_FOLLOW_DEPTH, bounded by the
        global MAX_LINKED_PAGES budget. Returns section dicts in BFS order."""
        queue = LinkQueue(max_depth=LINK_FOLLOW_DEPTH, budget=MAX_LINKED_PAGES,
                          exclude_urls=[main_url])
        for link in seed_links:
            queue.add(link['url'], 1, link.get('text', ''), parent_url=main_url)

        sections = []
        counter = 0
        while queue.pending():
            level = queue.pop_level()
            print(f"🔗 Capturing {len(level)} pages at depth {level[0].depth} "
                  f"(budget: {queue.accepted}/{queue.budget})")
            semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINKS)

            async def capture(target, index):
                async with semaphore:
                    return await self._capture_one(target, index, temp_dir)

            tasks = [capture(t, counter + i) for i, t in enumerate(level)]
            counter += len(level)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for target, result in zip(level, results):
                if isinstance(result, Exception) or result is None:
                    print(f"  ❌ Dropped {target.url}: {result}")
                    continue
                sections.append(result)
                # Feed this page's links into the next BFS level
                for child in result.pop('child_links'):
                    queue.add(child['url'], target.depth + 1,
                              child.get('text', ''), parent_url=target.url)
        return sections

    async def _capture_one(self, target, index, temp_dir):
        """Capture one linked page to PDF; insert a placeholder page if the
        page is blocked (captcha/HTTP error/timeout/too-small render)."""
        page = await self.browser_manager.create_new_page()
        if not page:
            return None
        reason = None
        title = target.link_text or target.url
        child_links = []
        pdf_path = temp_dir / f"linked_{index:03d}_{self.sanitize_filename(title)}.pdf"
        try:
            try:
                response = await page.goto(target.url,
                                           timeout=LINKED_PAGE_TIMEOUT * 1000,
                                           wait_until='domcontentloaded')
                await asyncio.sleep(2)
                status = response.status if response else None
                page_title = await page.title()
                if page_title:
                    title = page_title
                body_text = await page.evaluate(
                    "() => document.body ? document.body.innerText.slice(0, 3000) : ''")
                reason = detect_block(title, body_text, status)
            except Exception as e:
                reason = f"navigation failed ({type(e).__name__})"

            if reason is None:
                if target.depth < LINK_FOLLOW_DEPTH:
                    # Extract child links for the next BFS level while the DOM is live
                    content = await page.content()
                    soup = BeautifulSoup(content, 'html.parser')
                    child_links = self.extract_links(soup, target.url)

                await self.browser_manager.remove_header_elements_from_page(page)
                # Some sites (e.g. CNN) scroll content in an inner wrapper and set
                # overflow:hidden on <html>/<body>. Chromium's print pagination then
                # clips to one viewport and repeats it on every page. Force
                # document-level flow so the full article paginates normally.
                await page.evaluate(PRINT_FLOW_FIX_JS)
                await page.pdf(
                    path=str(pdf_path),
                    format='A4',
                    margin={'top': '0.75in', 'right': '0.75in',
                            'bottom': '0.75in', 'left': '0.75in'},
                    print_background=True,
                    prefer_css_page_size=False
                )
                if not pdf_path.exists() or pdf_path.stat().st_size < 10000:
                    reason = "rendered PDF too small (likely an error page)"
                    child_links = []

            if reason is not None:
                print(f"  ⚠️ {target.url}: {reason} — inserting placeholder page")
                await page.set_content(placeholder_html(target.url, title, reason))
                await page.pdf(path=str(pdf_path), format='A4',
                               print_background=True)

            print(f"  ✅ [{index}] depth {target.depth}: {title[:50]}"
                  f"{' (placeholder)' if reason else ''}")
            return {
                'url': target.url,
                'title': title,
                'pdf': str(pdf_path),
                'depth': target.depth,
                'parent_url': target.parent_url,
                'blocked': reason,
                'child_links': child_links,
            }
        except Exception as e:
            print(f"  ❌ Error capturing {target.url}: {e}")
            return None
        finally:
            await self.browser_manager.close_page(page)

    def sanitize_filename(self, filename):
        """Sanitize filename for file system"""
        if not filename:
            return "untitled"
        # Remove invalid characters
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'[-\s]+', '-', filename)
        return filename[:50]  # Limit length

    async def merge_sections(self, sections, output_filename):
        """Merge section PDFs in order, record each section's start page,
        then add the nested outline and internal links."""
        try:
            from PyPDF2 import PdfMerger, PdfReader
        except ImportError:
            print("❌ PyPDF2 not available — cannot merge")
            return False

        merger = PdfMerger()
        start_page = 0
        merged = []
        for section in sections:
            pdf_file = section['pdf']
            if not Path(pdf_file).exists():
                print(f"  ⚠️ Skipping missing file: {pdf_file}")
                continue
            try:
                num_pages = len(PdfReader(pdf_file).pages)
                merger.append(pdf_file)
                section['start_page'] = start_page
                start_page += num_pages
                merged.append(section)
                print(f"  ✅ Added {Path(pdf_file).name} ({num_pages} pages)")
            except Exception as e:
                print(f"  ❌ Failed to add {Path(pdf_file).name}: {e}")

        if not merged:
            print("❌ No PDFs could be added to merger")
            return False

        with open(output_filename, 'wb') as f:
            merger.write(f)
        merger.close()

        await self._add_internal_links(output_filename, merged)
        return True

    async def _add_internal_links(self, pdf_path, sections):
        """Add a nested bookmark outline and rewrite URI link annotations
        pointing at included sections as internal GoTo destinations."""
        tmp_path = None
        try:
            from PyPDF2 import PdfReader, PdfWriter
            from PyPDF2.generic import ArrayObject, NameObject, FloatObject

            url_to_page = {normalize_url(s['url']): s['start_page']
                           for s in sections if s.get('url')}

            print(f"🔗 Building outline and internal links "
                  f"({len(sections)} sections, {len(url_to_page)} target URLs)...")

            reader = PdfReader(pdf_path)
            writer = PdfWriter()
            for page in reader.pages:
                writer.add_page(page)

            # Nested outline: each section's bookmark hangs under its parent's
            # (level-1 pages under Main Article, level-2 under their level-1 page).
            outline_items = {}
            for s in sections:
                parent = outline_items.get(normalize_url(s.get('parent_url') or ''))
                item = writer.add_outline_item(s['title'], s['start_page'],
                                               parent=parent)
                outline_items[normalize_url(s['url'])] = item

            rewrites = 0
            for page in writer.pages:
                if '/Annots' not in page:
                    continue
                for annot_ref in page['/Annots']:
                    try:
                        annot = annot_ref.get_object()
                        if annot.get('/Subtype') != '/Link':
                            continue
                        action = annot.get('/A')
                        if action is None:
                            continue
                        action = action.get_object()
                        if action.get('/S') != '/URI':
                            continue
                        uri = str(action.get('/URI', ''))
                        norm = normalize_url(uri)
                        if norm not in url_to_page:
                            continue
                        target_idx = url_to_page[norm]
                        if target_idx >= len(writer.pages):
                            continue
                        target_page = writer.pages[target_idx]
                        target_height = float(target_page.mediabox.height)
                        del annot[NameObject('/A')]
                        annot[NameObject('/Dest')] = ArrayObject([
                            target_page.indirect_reference,
                            NameObject('/FitH'),
                            FloatObject(target_height)
                        ])
                        rewrites += 1
                    except Exception:
                        continue

            import os
            tmp_path = str(pdf_path) + '.tmp'
            with open(tmp_path, 'wb') as f:
                writer.write(f)
            os.replace(tmp_path, pdf_path)

            print(f"✅ Rewrote {rewrites} in-text links as internal navigation; "
                  f"{len(sections)} bookmarked sections")

        except Exception as e:
            print(f"⚠️ Could not add outline/internal links: {e}")
            import traceback
            traceback.print_exc()
            if tmp_path:
                Path(tmp_path).unlink(missing_ok=True)

    def get_processing_summary(self):
        """Get a summary of link processing"""
        return {
            'total_pages': len(self.link_to_page_map),
            'main_article_page': 1,
            'linked_pages': max(0, len(self.link_to_page_map) - 1),
            'page_map': dict(self.link_to_page_map)
        }
