#!/usr/bin/env python3
"""
Link processor for following and including linked pages in PDFs
"""

import asyncio
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from pathlib import Path

from config.settings import (
    FOLLOW_ARTICLE_LINKS, MAX_LINKED_PAGES, LINK_FOLLOW_DEPTH,
    ALLOWED_LINK_DOMAINS, SKIP_LINK_PATTERNS, LINKED_PAGE_TIMEOUT,
    REPLACE_LINKS_WITH_PDF_REFS, DEBUG_DIR
)


class LinkProcessor:
    """Processes links within articles and creates multi-page PDFs"""
    
    def __init__(self, browser_manager):
        self.browser_manager = browser_manager
        self.processed_links = set()
        self.link_to_page_map = {}  # Maps URLs to PDF page numbers
        self.current_page_number = 1
    
    async def process_article_with_links(self, article_url, output_filename):
        """Process an article and all its linked pages into a single clean PDF (no headers)"""
        if not FOLLOW_ARTICLE_LINKS:
            # Fall back to browser manager's proven method
            return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)
        
        print(f"🔗 Processing article with linked pages: {article_url}")
        
        try:
            # Step 1: Check if we can merge PDFs first
            merge_available = await self.test_merge_availability()
            if not merge_available:
                print("📄 PDF merging not available, using standard single-page PDF generation")
                return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)
            
            # Step 2: Load main article and extract links using browser manager navigation
            # Reset state for new article
            self.processed_links.clear()
            self.link_to_page_map.clear()
            self.current_page_number = 1
            
            # Navigate to main article using browser manager (no headers added)
            if not await self.browser_manager.navigate_to_url(article_url):
                print("❌ Failed to load main article")
                return False
            
            # Get page content and extract links
            page = self.browser_manager.get_page()
            content = await page.content()
            soup = BeautifulSoup(content, 'html.parser')
            links = self.extract_links(soup, article_url)
            
            if not links:
                print("📄 No relevant links found, using standard PDF generation")
                return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)
            
            # Step 3: Follow links and collect pages
            linked_pages = await self.follow_links(links)
            
            if not linked_pages:
                print("📄 No accessible linked pages, using standard PDF generation")
                return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)
            
            print(f"📄 Found {len(linked_pages)} accessible linked pages, creating clean multi-page PDF")
            
            # Step 3.5: Build page mapping for link replacement
            self.link_to_page_map[article_url] = 1
            for i, page_info in enumerate(linked_pages, 2):
                self.link_to_page_map[page_info['url']] = i
            
            print(f"🗺️ Page mapping created:")
            for url, page_num in self.link_to_page_map.items():
                print(f"  Page {page_num}: {url}")
            
            # Step 4: Replace links with page references if enabled
            if REPLACE_LINKS_WITH_PDF_REFS:
                print("🔗 Replacing links with PDF page references...")
                # Replace links in main article
                await self.replace_links_with_page_refs(article_url)
                # Replace links in each linked page
                for page_info in linked_pages:
                    await self.replace_links_with_page_refs(page_info['url'])
            
            # Step 5: Generate clean PDFs for each page (using browser manager)
            temp_dir = Path(DEBUG_DIR) / "temp_pdfs"
            temp_dir.mkdir(exist_ok=True)
            print(f"📁 Created temp directory: {temp_dir}")
            
            pdf_pages = []
            
            # Generate clean PDF for main page
            main_pdf = temp_dir / "page_1_main.pdf"
            print(f"🔗 Creating PDF for main article: {article_url}")
            if await self.browser_manager.convert_url_to_pdf(article_url, str(main_pdf)):
                if main_pdf.exists():
                    size = main_pdf.stat().st_size
                    print(f"  ✅ Main PDF created: {main_pdf.name} ({size} bytes)")
                    pdf_pages.append(str(main_pdf))
                else:
                    print(f"  ❌ Main PDF file not found after creation")
            else:
                print(f"  ❌ Failed to create main PDF")
            
            # Generate clean PDFs for linked pages
            print(f"🔗 Creating PDFs for {len(linked_pages)} linked pages...")
            for i, page_info in enumerate(linked_pages, 2):
                page_pdf = temp_dir / f"page_{i}_{self.sanitize_filename(page_info['title'])}.pdf"
                print(f"🔗 [{i-1}/{len(linked_pages)}] Creating PDF for: {page_info['title'][:50]}...")
                print(f"    URL: {page_info['url']}")
                print(f"    Output: {page_pdf.name}")
                
                if await self.browser_manager.convert_url_to_pdf(page_info['url'], str(page_pdf)):
                    if page_pdf.exists():
                        size = page_pdf.stat().st_size
                        print(f"    ✅ PDF created: {size} bytes")
                        pdf_pages.append(str(page_pdf))
                    else:
                        print(f"    ❌ PDF file not found after creation")
                else:
                    print(f"    ❌ Failed to create PDF")
            
            print(f"📊 PDF creation summary: {len(pdf_pages)} files created out of {len(linked_pages) + 1} attempted")
            
            # Step 5: Merge all clean PDFs
            if len(pdf_pages) > 1:
                print(f"🔀 Attempting to merge {len(pdf_pages)} PDFs...")
                success = await self.merge_pdfs(pdf_pages, output_filename)
                
                if success:
                    # Verify final file
                    if Path(output_filename).exists():
                        final_size = Path(output_filename).stat().st_size
                        print(f"✅ Final merged PDF: {final_size} bytes")
                    else:
                        print(f"❌ Final PDF file not found: {output_filename}")
                        success = False
                else:
                    print(f"❌ Merge failed, falling back to main article only")
                    success = False
            elif len(pdf_pages) == 1:
                print(f"📄 Only one PDF created, copying as final output...")
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
                print(f"✅ Generated clean multi-page PDF with {len(pdf_pages)} pages")
                return True
            else:
                print("❌ Failed to merge PDFs, falling back to single page")
                return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)
            
        except Exception as e:
            print(f"❌ Error processing article with links: {e}")
            # Always fall back to clean single-page conversion
            print("🔄 Falling back to clean single-page PDF conversion...")
            return await self.browser_manager.convert_url_to_pdf(article_url, output_filename)

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
            if not await self.browser_manager.navigate_to_url(url):
                print(f"❌ Failed to load page using browser manager")
                return None
            
            # Get page content after successful navigation
            page = self.browser_manager.get_page()
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
        """Follow links and collect page information"""
        linked_pages = []
        processed_count = 0
        
        print(f"🔗 Following {len(links)} links (max: {MAX_LINKED_PAGES})")
        
        for i, link in enumerate(links):
            if processed_count >= MAX_LINKED_PAGES:
                print(f"📄 Reached maximum linked pages limit ({MAX_LINKED_PAGES})")
                break
            
            url = link['url']
            link_text = link['text'][:50] + "..." if len(link['text']) > 50 else link['text']
            
            print(f"🔗 [{i+1}/{len(links)}] Following: {link_text} -> {url}")
            
            try:
                # Navigate to the linked page using browser manager
                if not await self.browser_manager.navigate_to_url(url):
                    print(f"  ❌ Failed to load page")
                    continue
                
                # Get page title
                page = self.browser_manager.get_page()
                title = await page.title()
                
                page_info = {
                    'url': url,
                    'title': title,
                    'link_text': link['text']
                }
                
                linked_pages.append(page_info)
                processed_count += 1
                
                print(f"  ✅ Successfully loaded: {title[:50]}...")
                
                # Wait between requests to be respectful
                await asyncio.sleep(1)
                
            except Exception as e:
                print(f"  ❌ Error loading {url}: {e}")
                continue
        
        print(f"✅ Successfully processed {len(linked_pages)} linked pages")
        
        if linked_pages:
            print("📋 Linked pages summary:")
            for i, page in enumerate(linked_pages, 1):
                print(f"  {i}. {page['title'][:60]}...")
        
        return linked_pages
    
    async def replace_links_with_page_refs(self, url):
        """Replace web links with PDF page references"""
        try:
            # Navigate to the page if not already there
            if not await self.browser_manager.navigate_to_url(url):
                print(f"  ❌ Failed to navigate to {url} for link replacement")
                return
            
            page = self.browser_manager.get_page()
            
            print(f"🔗 Processing link replacements for: {url}")
            print(f"📝 Available page mappings: {len(self.link_to_page_map)} pages")
            
            # JavaScript to replace links with page references
            replacement_script = f"""
            (() => {{
                const linkMap = {dict(self.link_to_page_map)};
                const links = document.querySelectorAll('a[href]');
                let replacedCount = 0;
                
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
                    
                    // Check if we have a page number for this URL
                    if (linkMap[absoluteUrl]) {{
                        const pageNum = linkMap[absoluteUrl];
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
            page = self.browser_manager.get_page()
            
            # Navigate to URL using browser manager's method
            if not await self.browser_manager.navigate_to_url(url):
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
            page = self.browser_manager.get_page()
            
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

    async def merge_pdfs(self, pdf_files, output_filename):
        """Merge multiple PDF files into one"""
        print(f"🔀 Attempting to merge {len(pdf_files)} PDF files...")
        
        # List all files to be merged
        for i, pdf_file in enumerate(pdf_files, 1):
            if Path(pdf_file).exists():
                size = Path(pdf_file).stat().st_size
                print(f"  {i}. {Path(pdf_file).name} ({size} bytes)")
            else:
                print(f"  {i}. {Path(pdf_file).name} ❌ FILE MISSING")
        
        try:
            # Try using PyPDF2 if available
            try:
                from PyPDF2 import PdfMerger
                print("📦 Using PyPDF2 for merging...")
                
                merger = PdfMerger()
                
                merged_count = 0
                for pdf_file in pdf_files:
                    if Path(pdf_file).exists():
                        try:
                            merger.append(pdf_file)
                            merged_count += 1
                            print(f"  ✅ Added {Path(pdf_file).name} to merger")
                        except Exception as e:
                            print(f"  ❌ Failed to add {Path(pdf_file).name}: {e}")
                    else:
                        print(f"  ⚠️ Skipping missing file: {pdf_file}")
                
                if merged_count == 0:
                    print("❌ No PDFs could be added to merger")
                    return False
                
                with open(output_filename, 'wb') as output_file:
                    merger.write(output_file)
                
                merger.close()
                
                # Verify the merged file
                if Path(output_filename).exists():
                    final_size = Path(output_filename).stat().st_size
                    print(f"✅ Merged {merged_count} PDFs using PyPDF2 -> {final_size} bytes")
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
            return False
    
    def get_processing_summary(self):
        """Get a summary of link processing"""
        return {
            'total_pages': len(self.link_to_page_map),
            'main_article_page': 1,
            'linked_pages': len(self.link_to_page_map) - 1,
            'page_map': dict(self.link_to_page_map)
        }
