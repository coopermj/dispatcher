#!/usr/bin/env python3
"""
Link processor for following and including linked pages in PDFs
"""

import asyncio
import json
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path

from config.settings import (
    FOLLOW_ARTICLE_LINKS, MAX_LINKED_PAGES, LINK_FOLLOW_DEPTH,
    ALLOWED_LINK_DOMAINS, SKIP_LINK_PATTERNS, LINKED_PAGE_TIMEOUT,
    REPLACE_LINKS_WITH_PDF_REFS, DEBUG_DIR, SKIP_DOMAINS, MAX_CONCURRENT_LINKS
)


class LinkProcessor:
    """Processes links within articles and creates multi-page PDFs"""

    def __init__(self, browser_manager):
        self.browser_manager = browser_manager
        self.processed_links = set()
        self.link_to_page_map = {}  # Maps URLs to PDF page numbers
        self.current_page_number = 1
        self._active_page = None  # Dedicated page for current processing
        self._owns_page = False   # Whether we created the page and should close it

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
            self.current_page_number = 1

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

            # Set up temp directory for PDFs
            temp_dir = Path(DEBUG_DIR) / "temp_pdfs"
            temp_dir.mkdir(exist_ok=True)
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
                return True

            # Step 3: Follow links and collect page info (this navigates away from main article)
            # We already have the main PDF saved, so this is safe now
            linked_pages = await self.follow_links(links)

            if not linked_pages:
                print("📄 No accessible linked pages, using main article PDF only")
                import shutil
                shutil.move(str(main_pdf), output_filename)
                return True

            print(f"📄 Found {len(linked_pages)} accessible linked pages, creating multi-page PDF")

            # Build page mapping for bookmarks
            self.link_to_page_map[article_url] = 1
            for i, page_info in enumerate(linked_pages, 2):
                self.link_to_page_map[page_info['url']] = i

            print(f"🗺️ Page mapping created:")
            for url, page_num in self.link_to_page_map.items():
                print(f"  Page {page_num}: {url[:70]}...")

            # Step 4: Generate PDFs for linked pages
            # Skip link replacement - it causes too many navigation issues
            print(f"🔗 Creating PDFs for {len(linked_pages)} linked pages...")
            page_titles = ["Main Article"]
            for i, page_info in enumerate(linked_pages, 2):
                page_pdf = temp_dir / f"page_{i}_{self.sanitize_filename(page_info['title'])}.pdf"
                print(f"🔗 [{i-1}/{len(linked_pages)}] Creating PDF for: {page_info['title'][:50]}...")
                print(f"    URL: {page_info['url']}")

                if await self.browser_manager.convert_url_to_pdf_with_page(
                    page_info['url'], str(page_pdf), self._active_page
                ):
                    if page_pdf.exists():
                        size = page_pdf.stat().st_size
                        # Only include if it's a reasonable size (not an error page)
                        if size > 10000:  # More than 10KB suggests real content
                            print(f"    ✅ PDF created: {size} bytes")
                            pdf_pages.append(str(page_pdf))
                            page_titles.append(page_info.get('title', 'Linked Article'))
                        else:
                            print(f"    ⚠️ PDF too small ({size} bytes), likely error page - skipping")
                            page_pdf.unlink(missing_ok=True)
                    else:
                        print(f"    ❌ PDF file not found after creation")
                else:
                    print(f"    ❌ Failed to create PDF")

            print(f"📊 PDF creation summary: {len(pdf_pages)} files created")

            # Step 5: Merge all PDFs with bookmarks
            if len(pdf_pages) > 1:
                print(f"🔀 Attempting to merge {len(pdf_pages)} PDFs...")
                success = await self.merge_pdfs(pdf_pages, output_filename, page_titles=page_titles)

                if success and Path(output_filename).exists():
                    final_size = Path(output_filename).stat().st_size
                    print(f"✅ Final merged PDF: {final_size} bytes")
                else:
                    print(f"❌ Merge failed, using main article only")
                    import shutil
                    shutil.copy(pdf_pages[0], output_filename)
                    success = True
            elif len(pdf_pages) == 1:
                print(f"📄 Only main PDF created, using as final output...")
                import shutil
                shutil.move(pdf_pages[0], output_filename)
                success = True
            else:
                print(f"❌ No PDFs were created successfully")
                success = False

            # Cleanup temp files
            for pdf_file in pdf_pages:
                try:
                    Path(pdf_file).unlink(missing_ok=True)
                except:
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
    
    async def load_and_analyze_page(self, url, is_main=False):
        """Load a page and analyze its links using browser manager's approach"""
        try:
            print(f"📄 Loading page: {url}")

            # Use browser manager's navigation method for consistency
            if not await self.browser_manager.navigate_to_url_with_page(url, self._active_page):
                print(f"❌ Failed to load page using browser manager")
                return None

            # Get page content after successful navigation
            page = self._active_page
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            # Extract links from the page
            links = self.extract_links(soup, url)
            
            # Assign page number
            page_number = self.current_page_number
            self.link_to_page_map[url] = page_number
            self.current_page_number += 1
            
            return {
                'url': url,
                'page_number': page_number,
                'links': links,
                'title': await page.title(),
                'is_main': is_main
            }
            
        except Exception as e:
            print(f"❌ Error loading page {url}: {e}")
            return None
    
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
            # Skip very short link text (likely navigation)
            if len(link_text) < 5:
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
                    'related', 'previously', 'earlier', 'background', 'context'
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
    
    async def follow_links(self, links):
        """Follow links and collect page information (with parallel processing)"""
        print(f"🔗 Following {len(links)} links (max: {MAX_LINKED_PAGES})")
        print(f"⚡ Using parallel link following with up to {MAX_CONCURRENT_LINKS} concurrent requests")

        # Limit links to max allowed
        links_to_process = links[:MAX_LINKED_PAGES]

        # Create a semaphore to limit concurrency
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_LINKS)

        async def follow_single_link(link, index):
            async with semaphore:
                return await self._follow_single_link(link, index, len(links_to_process))

        # Create tasks for all links
        tasks = [follow_single_link(link, i) for i, link in enumerate(links_to_process)]

        # Execute in parallel and gather results
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter successful results
        linked_pages = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"  ❌ Error processing link {i+1}: {result}")
            elif result is not None:
                linked_pages.append(result)

        print(f"✅ Successfully processed {len(linked_pages)} linked pages")

        if linked_pages:
            print("📋 Linked pages summary:")
            for i, page in enumerate(linked_pages, 1):
                print(f"  {i}. {page['title'][:60]}...")

        return linked_pages

    async def _follow_single_link(self, link, index, total):
        """Follow a single link using a new browser page"""
        page = None
        url = link['url']
        link_text = link['text'][:50] + "..." if len(link['text']) > 50 else link['text']

        print(f"🔗 [{index+1}/{total}] Following: {link_text}")

        try:
            # Create a new page for this link
            page = await self.browser_manager.create_new_page()
            if not page:
                # Fallback to main page (sequential)
                if not await self.browser_manager.navigate_to_url(url):
                    print(f"  ❌ Failed to load page")
                    return None
                main_page = self.browser_manager.get_page()
                title = await main_page.title()
            else:
                # Use the new page
                await page.goto(url, timeout=30000)
                await asyncio.sleep(2)
                title = await page.title()

            page_info = {
                'url': url,
                'title': title,
                'link_text': link['text']
            }

            print(f"  ✅ Successfully loaded: {title[:50]}...")
            return page_info

        except Exception as e:
            print(f"  ❌ Error loading {url}: {e}")
            return None
        finally:
            # Clean up the page
            if page:
                await self.browser_manager.close_page(page)
    
    async def replace_links_with_page_refs(self, url):
        """Replace web links with PDF page references"""
        try:
            # Navigate to the page if not already there
            if not await self.browser_manager.navigate_to_url_with_page(url, self._active_page):
                print(f"  ❌ Failed to navigate to {url} for link replacement")
                return

            page = self._active_page
            
            print(f"🔗 Processing link replacements for: {url}")
            print(f"📝 Available page mappings: {len(self.link_to_page_map)} pages")
            
            # JavaScript to replace links with page references
            link_map_json = json.dumps(dict(self.link_to_page_map))
            replacement_script = f"""
            (() => {{
                const linkMap = {link_map_json};
                const links = document.querySelectorAll('a[href]');
                let replacedCount = 0;

                // Normalize URL for comparison (strip trailing slash, query params, fragments)
                const normalizeUrl = (url) => {{
                    if (!url) return '';
                    return url.split('?')[0].split('#')[0].replace(/\\/$/, '');
                }};

                // Build normalized linkMap for lookup
                const normalizedLinkMap = {{}};
                for (const [url, page] of Object.entries(linkMap)) {{
                    normalizedLinkMap[normalizeUrl(url)] = page;
                }}

                console.log('Link replacement script running...');
                console.log('Available pages:', Object.keys(linkMap).length);
                console.log('Total links found:', links.length);

                links.forEach((link, index) => {{
                    const href = link.getAttribute('href');
                    let absoluteUrl;

                    // Convert to absolute URL
                    if (href.startsWith('http')) {{
                        absoluteUrl = href;
                    }} else if (href.startsWith('/')) {{
                        absoluteUrl = window.location.origin + href;
                    }} else if (href.startsWith('#')) {{
                        // Skip fragment links
                        return;
                    }} else {{
                        const base = window.location.href.split('/').slice(0, -1).join('/');
                        absoluteUrl = base + '/' + href;
                    }}

                    // Normalize and check if we have a page number for this URL
                    const normalizedAbsoluteUrl = normalizeUrl(absoluteUrl);
                    if (normalizedLinkMap[normalizedAbsoluteUrl]) {{
                        const pageNum = normalizedLinkMap[normalizedAbsoluteUrl];
                        const linkText = link.textContent.trim();
                        
                        console.log(`Replacing link ${{index}}: "${{linkText}}" -> Page ${{pageNum}}`);
                        
                        // Replace link with page reference
                        link.style.color = '#0066cc';
                        link.style.textDecoration = 'none';
                        link.style.borderBottom = '1px dotted #0066cc';
                        link.style.backgroundColor = '#f0f8ff';
                        link.style.padding = '2px 4px';
                        link.style.borderRadius = '3px';
                        link.setAttribute('href', '#');
                        link.setAttribute('title', `See page ${{pageNum}} in this PDF`);
                        
                        // Add page reference after link text
                        if (!linkText.includes('(p.')) {{
                            link.innerHTML = linkText + ` <span style="font-size: 0.9em; color: #666; font-weight: bold;">(p. ${{pageNum}})</span>`;
                        }}
                        
                        replacedCount++;
                    }}
                }});
                
                console.log('Replaced', replacedCount, 'links with page references');
                return replacedCount;
            }})();
            """
            
            result = await page.evaluate(replacement_script)
            print(f"  ✅ Replaced {result} links with page references")
            
            # Wait a moment for the changes to take effect
            await asyncio.sleep(1)
            
        except Exception as e:
            print(f"  ⚠️ Error replacing links on {url}: {e}")
    
    async def generate_multi_page_pdf(self, main_url, linked_pages, output_filename):
        """Generate a multi-page PDF with main article and linked pages (no headers)"""
        try:
            print(f"📄 Generating multi-page PDF with {len(linked_pages) + 1} pages (no headers)...")
            
            # Create temporary PDF files for each page
            temp_dir = Path(DEBUG_DIR) / "temp_pdfs"
            temp_dir.mkdir(exist_ok=True)
            
            pdf_pages = []
            
            # Generate PDF for main page (no header)
            main_pdf = temp_dir / f"page_1_main.pdf"
            await self.generate_single_page_pdf(main_url, str(main_pdf), is_main=True)
            pdf_pages.append(str(main_pdf))
            
            # Generate PDFs for linked pages (no headers)
            for i, page_info in enumerate(linked_pages, 2):
                page_pdf = temp_dir / f"page_{i}_{self.sanitize_filename(page_info['title'])}.pdf"
                success = await self.generate_single_page_pdf(page_info['url'], str(page_pdf))
                if success:
                    pdf_pages.append(str(page_pdf))
            
            # Merge all PDFs into one
            if len(pdf_pages) > 1:
                success = await self.merge_pdfs(pdf_pages, output_filename)
            else:
                # Just rename the single PDF
                import shutil
                shutil.move(pdf_pages[0], output_filename)
                success = True
            
            # Cleanup temp files
            for pdf_file in pdf_pages:
                try:
                    Path(pdf_file).unlink(missing_ok=True)
                except:
                    pass
            
            if success:
                print(f"✅ Generated clean multi-page PDF: {output_filename}")
                return True
            else:
                print("❌ Failed to generate multi-page PDF")
                return False
                
        except Exception as e:
            print(f"❌ Error generating multi-page PDF: {e}")
            return False
    
    async def generate_single_page_pdf(self, url, output_file, is_main=False):
        """Generate a PDF for a single page using the browser manager's proven approach"""
        try:
            page = self._active_page

            # Navigate to URL using browser manager's method
            if not await self.browser_manager.navigate_to_url_with_page(url, self._active_page):
                return False

            # Use browser manager's proven cleanup approach
            if is_main:
                # For main page, use full cleanup
                await self.browser_manager.remove_header_elements()
            else:
                # For linked pages, use lighter cleanup to preserve content
                await self.remove_navigation_elements()
            
            # Use browser manager's PDF generation
            await page.pdf(
                path=output_file,
                format='A4',
                margin={'top': '1in', 'right': '0.75in', 'bottom': '0.75in', 'left': '0.75in'},
                print_background=True,
                prefer_css_page_size=False
            )
            
            return True
            
        except Exception as e:
            print(f"❌ Error generating PDF for {url}: {e}")
            return False

    async def remove_navigation_elements(self):
        """Remove navigation elements from linked pages"""
        try:
            page = self._active_page
            
            cleanup_script = """
            (() => {
                // Remove navigation elements
                ['nav', 'header', 'footer', '.navigation', '.navbar', '.menu'].forEach(selector => {
                    document.querySelectorAll(selector).forEach(e => e.remove());
                });
                
                // Remove ads and sidebars
                ['.ad', '.advertisement', '.sidebar', '.widget'].forEach(selector => {
                    document.querySelectorAll(selector).forEach(e => e.remove());
                });
            })();
            """
            
            await page.evaluate(cleanup_script)
            
        except Exception as e:
            print(f"⚠️ Error removing navigation elements: {e}")
    
    def sanitize_filename(self, filename):
        """Sanitize filename for file system"""
        if not filename:
            return "untitled"
        # Remove invalid characters
        filename = re.sub(r'[^\w\s-]', '', filename)
        filename = re.sub(r'[-\s]+', '-', filename)
        return filename[:50]  # Limit length

    async def merge_pdfs(self, pdf_files, output_filename, page_titles=None):
        """Merge multiple PDF files into one with bookmarks and internal links"""
        print(f"🔀 Attempting to merge {len(pdf_files)} PDF files...")

        # List all files to be merged
        for i, pdf_file in enumerate(pdf_files, 1):
            if Path(pdf_file).exists():
                size = Path(pdf_file).stat().st_size
                print(f"  {i}. {Path(pdf_file).name} ({size} bytes)")
            else:
                print(f"  {i}. {Path(pdf_file).name} ❌ FILE MISSING")

        try:
            from PyPDF2 import PdfMerger, PdfReader, PdfWriter
            print("📦 Using PyPDF2 for merging with bookmarks...")

            merger = PdfMerger()

            merged_count = 0
            page_number = 0
            bookmark_info = []  # Track bookmarks for later link creation

            for i, pdf_file in enumerate(pdf_files):
                if Path(pdf_file).exists():
                    try:
                        # Get page count of this PDF
                        reader = PdfReader(pdf_file)
                        num_pages = len(reader.pages)

                        # Determine bookmark title
                        if page_titles and i < len(page_titles):
                            title = page_titles[i]
                        elif i == 0:
                            title = "Main Article"
                        else:
                            # Extract title from filename
                            title = Path(pdf_file).stem.replace('-', ' ').replace('_', ' ')
                            # Remove page number prefix like "page_2_"
                            if title.startswith('page '):
                                parts = title.split(' ', 2)
                                if len(parts) > 2:
                                    title = parts[2]

                        # Add PDF with bookmark
                        merger.append(pdf_file, outline_item=title)
                        bookmark_info.append({
                            'title': title,
                            'start_page': page_number,
                            'num_pages': num_pages
                        })

                        merged_count += 1
                        page_number += num_pages
                        print(f"  ✅ Added {Path(pdf_file).name} ({num_pages} pages) - Bookmark: {title[:40]}...")
                    except Exception as e:
                        print(f"  ❌ Failed to add {Path(pdf_file).name}: {e}")
                else:
                    print(f"  ⚠️ Skipping missing file: {pdf_file}")

            if merged_count == 0:
                print("❌ No PDFs could be added to merger")
                return False

            # Write merged PDF
            with open(output_filename, 'wb') as output_file:
                merger.write(output_file)

            merger.close()

            # Verify the merged file
            if Path(output_filename).exists():
                final_size = Path(output_filename).stat().st_size
                print(f"✅ Merged {merged_count} PDFs with bookmarks -> {final_size} bytes")
                print(f"📑 Added {len(bookmark_info)} bookmarks for navigation")

                # Now add internal links to the merged PDF
                await self._add_internal_links(output_filename, bookmark_info)

                return True
            else:
                print("❌ Merged file was not created")
                return False

        except ImportError:
            print("📦 PyPDF2 not available, trying alternative method...")

            # Fallback: Use system commands if available
            try:
                import subprocess

                # Try using pdfunite (part of poppler-utils)
                cmd = ['pdfunite'] + pdf_files + [output_filename]
                print(f"🔧 Running: {' '.join(cmd)}")
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

                if result.returncode == 0:
                    if Path(output_filename).exists():
                        final_size = Path(output_filename).stat().st_size
                        print(f"✅ Merged {len(pdf_files)} PDFs using pdfunite -> {final_size} bytes")
                        return True
                    else:
                        print("❌ pdfunite succeeded but no output file created")
                        return False
                else:
                    print(f"❌ pdfunite failed: {result.stderr}")

            except (subprocess.TimeoutExpired, FileNotFoundError):
                print("❌ pdfunite not available")

            # Final fallback: Return False to indicate merge failed
            print("❌ PDF merging not available")
            return False

        except Exception as e:
            print(f"❌ Error merging PDFs: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _add_internal_links(self, pdf_path, bookmark_info):
        """Add internal link annotations to the first page pointing to each section"""
        if len(bookmark_info) <= 1:
            print("📑 Only one section, no internal links needed")
            return

        try:
            from PyPDF2 import PdfReader, PdfWriter
            from PyPDF2.generic import (
                DictionaryObject, ArrayObject, NameObject,
                NumberObject, FloatObject
            )

            print("🔗 Adding internal navigation links...")

            reader = PdfReader(pdf_path)
            writer = PdfWriter()

            # Copy all pages
            for page in reader.pages:
                writer.add_page(page)

            # Manually copy bookmarks (clone_document_from_reader loses them)
            if reader.outline:
                for item in reader.outline:
                    if not isinstance(item, list):
                        writer.add_outline_item(item.title, item.page)

            # Get first page and its dimensions
            first_page = writer.pages[0]
            page_width = float(first_page.mediabox.width)
            page_height = float(first_page.mediabox.height)

            # Initialize annotations array if not present
            if '/Annots' not in first_page:
                first_page[NameObject('/Annots')] = ArrayObject()
            annots = first_page['/Annots']

            # Create link annotations for each linked article
            links_added = 0
            for i, info in enumerate(bookmark_info[1:], 1):  # Skip main article (index 0)
                dest_page = info['start_page']
                if dest_page >= len(writer.pages):
                    continue

                # Create link annotation
                link_annot = DictionaryObject()
                link_annot[NameObject('/Type')] = NameObject('/Annot')
                link_annot[NameObject('/Subtype')] = NameObject('/Link')

                # Link rectangle - position at bottom of first page as a "table of contents" area
                # Each link is a horizontal bar
                y_pos = 100 - (i * 20)  # Stack from bottom up
                if y_pos < 20:
                    y_pos = 20  # Minimum position

                link_annot[NameObject('/Rect')] = ArrayObject([
                    FloatObject(50),
                    FloatObject(y_pos),
                    FloatObject(page_width - 50),
                    FloatObject(y_pos + 18)
                ])

                # Destination: go to the start page of this section
                link_annot[NameObject('/Dest')] = ArrayObject([
                    writer.pages[dest_page].indirect_reference,
                    NameObject('/FitH'),
                    FloatObject(page_height)
                ])

                # Border style (invisible border)
                link_annot[NameObject('/Border')] = ArrayObject([
                    NumberObject(0), NumberObject(0), NumberObject(0)
                ])

                # Highlight style when clicked
                link_annot[NameObject('/H')] = NameObject('/I')  # Invert

                # Add annotation directly to the page's annotations array
                annots.append(link_annot)
                links_added += 1

            # Write the updated PDF back to the file
            if links_added > 0:
                with open(pdf_path, 'wb') as output_file:
                    writer.write(output_file)
                print(f"✅ Added {links_added} internal link annotations to PDF")

            print(f"📑 Bookmarks provide navigation to {len(bookmark_info)} sections")
            print("💡 Use PDF viewer's bookmark panel OR click links at bottom of first page")

        except Exception as e:
            print(f"⚠️ Could not add link annotations: {e}")
            import traceback
            traceback.print_exc()
            # Continue anyway - bookmarks still work
    
    def get_processing_summary(self):
        """Get a summary of link processing"""
        return {
            'total_pages': len(self.link_to_page_map),
            'main_article_page': 1,
            'linked_pages': len(self.link_to_page_map) - 1,
            'page_map': dict(self.link_to_page_map)
        }
