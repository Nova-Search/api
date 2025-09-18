from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
import asyncio
import aiohttp
from urllib.parse import urljoin, urlparse, urlunparse
from bs4 import BeautifulSoup
import hashlib
import re
from datetime import datetime, timedelta
import threading
import time
import random
from typing import List, Optional
import os
import sys

if __name__ == "__main__":
    print("\033[1;34mYou ran the API wrong; check README.md for more info\033[0m")
    sys.exit(1)

app = FastAPI()

# Constants
DB_PATH = 'links.db'
FAVICON_DIR = 'favicons'  # Directory where favicons are stored

# Dictionary to store request counts and timestamps for rate limiting
request_counts = {}

# Rate limit configuration
RATE_LIMITS = {
    "GET": 15,  # requests per minute
    "POST": 5  # requests per minute
}
TIME_WINDOW = timedelta(minutes=1)  # time window

def check_db_exists():
    """Check if the database exists."""
    if not os.path.exists(DB_PATH):
        print("\033[1;33mWarning: Database does not exist.\033[0m")
        choice = input("Do you want to recreate the database? (yes/no): ").strip().lower()
        if choice == 'yes':
            create_db()
        else:
            print("Stopping API.")
            sys.exit(1)

def create_db():
    """Create the database and necessary tables."""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # Enhanced pages table with full-text search support
    cursor.execute('''
        CREATE TABLE pages (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            title TEXT,
            description TEXT,
            content_text TEXT,
            keywords TEXT,
            priority INTEGER DEFAULT 0,
            favicon_id TEXT,
            last_crawled TIMESTAMP,
            domain TEXT,
            page_rank REAL DEFAULT 0.0,
            content_hash TEXT,
            language TEXT,
            status_code INTEGER,
            redirect_url TEXT
        )
    ''')
    
    # Create full-text search virtual table
    cursor.execute('''
        CREATE VIRTUAL TABLE pages_fts USING fts5(
            url, title, description, content_text, keywords,
            content='pages',
            content_rowid='id'
        )
    ''')
    
    # Triggers to keep FTS table in sync
    cursor.execute('''
        CREATE TRIGGER pages_ai AFTER INSERT ON pages BEGIN
            INSERT INTO pages_fts(rowid, url, title, description, content_text, keywords)
            VALUES (new.id, new.url, new.title, new.description, new.content_text, new.keywords);
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER pages_ad AFTER DELETE ON pages BEGIN
            DELETE FROM pages_fts WHERE rowid = old.id;
        END
    ''')
    
    cursor.execute('''
        CREATE TRIGGER pages_au AFTER UPDATE ON pages BEGIN
            UPDATE pages_fts SET url = new.url, title = new.title, 
                                 description = new.description, content_text = new.content_text,
                                 keywords = new.keywords
            WHERE rowid = new.id;
        END
    ''')
    
    # Links table for tracking hyperlinks between pages
    cursor.execute('''
        CREATE TABLE links (
            id INTEGER PRIMARY KEY,
            from_url TEXT NOT NULL,
            to_url TEXT NOT NULL,
            anchor_text TEXT,
            discovered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(from_url, to_url)
        )
    ''')
    
    # Crawl queue for managing infinite crawling
    cursor.execute('''
        CREATE TABLE crawl_queue (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL UNIQUE,
            priority INTEGER DEFAULT 0,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            attempts INTEGER DEFAULT 0,
            last_attempt TIMESTAMP,
            next_crawl TIMESTAMP,
            source_url TEXT
        )
    ''')
    
    # Domain verification table for webmaster console
    cursor.execute('''
        CREATE TABLE domain_verifications (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL UNIQUE,
            verification_token TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            verified_at TIMESTAMP
        )
    ''')
    
    # Create indexes for better performance
    cursor.execute('CREATE INDEX idx_pages_url ON pages(url)')
    cursor.execute('CREATE INDEX idx_pages_domain ON pages(domain)')
    cursor.execute('CREATE INDEX idx_pages_last_crawled ON pages(last_crawled)')
    cursor.execute('CREATE INDEX idx_pages_priority ON pages(priority)')
    cursor.execute('CREATE INDEX idx_links_from_url ON links(from_url)')
    cursor.execute('CREATE INDEX idx_links_to_url ON links(to_url)')
    cursor.execute('CREATE INDEX idx_crawl_queue_priority ON crawl_queue(priority, next_crawl)')
    cursor.execute('CREATE INDEX idx_domain_verifications_domain ON domain_verifications(domain, status)')
    
    conn.commit()
    conn.close()
    print("Enhanced database created successfully.")

# Check if the database exists at startup
check_db_exists()

# Rate limit middleware
@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    ip = request.headers.get("CF-Connecting-IP", request.client.host)
    now = datetime.now()
    method = request.method

    # Exempt /favicon endpoint from rate limiting
    if request.url.path.startswith("/favicon"):
        return await call_next(request)

    if ip not in request_counts:
        request_counts[ip] = {}

    if method not in request_counts[ip]:
        request_counts[ip][method] = {"count": 1, "timestamp": now}
    else:
        request_info = request_counts[ip][method]
        if now - request_info["timestamp"] > TIME_WINDOW:
            request_counts[ip][method] = {"count": 1, "timestamp": now}
        else:
            request_info["count"] += 1
            if request_info["count"] > RATE_LIMITS[method]:
                return JSONResponse(status_code=429, content={"detail": "Rate limit exceeded"})

    response = await call_next(request)
    return response

# Enable CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_db_connection():
    """Establish a new database connection and enable WAL mode."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL;')  # Enable WAL mode
    return conn

@app.get("/")
def read_root():
    return [{"message": "If you can see this, the Nova Search API is working.", "documentation": "https://docs.novasearch.xyz"}]

@app.get("/search")
def search(query: str = Query(...), page: int = 1, page_size: int = 15):
    """Enhanced search with semantic understanding and question-answering capabilities."""
    if not query.strip():
        raise HTTPException(status_code=400, detail="Query cannot be blank.")

    offset = (page - 1) * page_size
    query_lower = query.lower().strip()
    
    # Detect if this is a question
    question_words = ['what', 'how', 'why', 'when', 'where', 'who', 'which', 'can', 'should', 'is', 'are', 'does', 'do']
    is_question = any(query_lower.startswith(word) for word in question_words) or query.endswith('?')

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        if is_question:
            # Use semantic search for questions
            results = semantic_search_simple(cursor, query, page_size, offset)
        else:
            # Use enhanced traditional search for keywords
            results = enhanced_keyword_search_simple(cursor, query, page_size, offset)
        
        return results

    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

def semantic_search_simple(cursor, query, page_size, offset):
    """Perform semantic search for question-like queries using enhanced LIKE queries."""
    # Extract key concepts from the question
    query_words = extract_key_terms(query)
    
    # Build WHERE clause for multiple terms
    where_conditions = []
    params = []
    
    for word in query_words[:5]:  # Limit to first 5 key terms
        where_conditions.append("(title LIKE ? OR description LIKE ? OR content_text LIKE ? OR keywords LIKE ?)")
        params.extend([f'%{word}%', f'%{word}%', f'%{word}%', f'%{word}%'])
    
    where_clause = " OR ".join(where_conditions) if where_conditions else "1=0"
    
    # Count total results
    count_query = f"SELECT COUNT(*) FROM pages WHERE {where_clause}"
    cursor.execute(count_query, params)
    total_results = cursor.fetchone()[0]
    total_pages = (total_results + page_size - 1) // page_size

    # Get ranked results with relevance scoring
    search_query = f'''
        SELECT url, title, description, keywords, favicon_id, last_crawled, 
               page_rank, domain,
               (priority + page_rank * 10 + 
                CASE 
                    WHEN title LIKE ? THEN 20
                    WHEN description LIKE ? THEN 15
                    WHEN keywords LIKE ? THEN 10
                    ELSE 5
                END) as relevance_score
        FROM pages
        WHERE {where_clause}
        ORDER BY relevance_score DESC, priority DESC, page_rank DESC
        LIMIT ? OFFSET ?
    '''
    
    # Add primary query term for relevance scoring
    primary_term = query_words[0] if query_words else query
    score_params = [f'%{primary_term}%', f'%{primary_term}%', f'%{primary_term}%']
    
    cursor.execute(search_query, score_params + params + [page_size, offset])
    results = cursor.fetchall()
    
    current_page = (offset // page_size) + 1
    return format_search_results(results, total_results, total_pages, current_page, True)

def enhanced_keyword_search_simple(cursor, query, page_size, offset):
    """Enhanced keyword search with better relevance ranking."""
    # Count total results
    cursor.execute('''
        SELECT COUNT(*)
        FROM pages
        WHERE title LIKE ? OR description LIKE ? OR content_text LIKE ? OR keywords LIKE ? OR url LIKE ?
    ''', (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%'))
    total_results = cursor.fetchone()[0]
    
    if total_results == 0:
        raise HTTPException(status_code=410, detail="No results found.")
    
    total_pages = (total_results + page_size - 1) // page_size
    
    # Enhanced search with relevance scoring
    cursor.execute('''
        SELECT url, title, description, keywords, favicon_id, last_crawled, 
               page_rank, domain,
               (priority + page_rank * 10 + 
                CASE 
                    WHEN title LIKE ? THEN 20
                    WHEN description LIKE ? THEN 15
                    WHEN keywords LIKE ? THEN 10
                    WHEN content_text LIKE ? THEN 8
                    ELSE 5
                END) as relevance_score
        FROM pages
        WHERE title LIKE ? OR description LIKE ? OR content_text LIKE ? OR keywords LIKE ? OR url LIKE ?
        ORDER BY relevance_score DESC, priority DESC, page_rank DESC
        LIMIT ? OFFSET ?
    ''', (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%',  # For scoring
              f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%',  # For WHERE
              page_size, offset))
    
    results = cursor.fetchall()
    current_page = (offset // page_size) + 1
    return format_search_results(results, total_results, total_pages, current_page, False)

def extract_key_terms(query):
    """Extract key terms from a query for semantic search."""
    import re
    # Remove common stop words and question words
    stop_words = {'what', 'how', 'why', 'when', 'where', 'who', 'which', 'can', 'should', 
                  'is', 'are', 'does', 'do', 'the', 'a', 'an', 'and', 'or', 'but', 
                  'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'about'}
    
    # Extract words, remove punctuation
    words = re.findall(r'\b\w+\b', query.lower())
    key_terms = [word for word in words if word not in stop_words and len(word) > 2]
    
    return key_terms[:10]  # Limit to top 10 terms

def format_search_results(results, total_results, total_pages, page, is_semantic):
    """Format search results for API response."""
    if not results:
        raise HTTPException(status_code=410, detail="No results found.")

    return {
        "current_page": page,
        "total_pages": total_pages,
        "total_results": total_results,
        "search_type": "semantic" if is_semantic else "keyword",
        "results": [
            {
                "url": row[0],
                "title": row[1],
                "description": row[2],
                "keywords": row[3],
                "favicon_id": row[4],
                "last_crawled": row[5],
                "domain": row[7] if len(row) > 7 else extract_domain(row[0]),
                "relevance_score": float(row[8]) if len(row) > 8 else 0.0
            }
            for row in results
        ]
    }

def extract_domain(url):
    """Extract domain from URL."""
    from urllib.parse import urlparse
    try:
        return urlparse(url).netloc
    except:
        return ""

# Enhanced Crawling System
class InfiniteCrawler:
    """Infinite web crawler with link discovery and intelligent queuing."""
    
    def __init__(self):
        self.session = None
        self.crawl_delay = 1  # Default delay between requests
        self.max_concurrent = 5
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:89.0) Gecko/20100101 Firefox/89.0"
        ]
        self.running = False
        
    async def start_crawling(self):
        """Start the infinite crawling process."""
        if self.running:
            return
        
        self.running = True
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30),
            connector=aiohttp.TCPConnector(limit=self.max_concurrent)
        ) as session:
            self.session = session
            
            while self.running:
                try:
                    # Get next URLs to crawl
                    urls_to_crawl = self.get_next_crawl_batch()
                    
                    if not urls_to_crawl:
                        await asyncio.sleep(60)  # Wait if no URLs to crawl
                        continue
                    
                    # Process URLs concurrently
                    tasks = [self.crawl_url(url_data) for url_data in urls_to_crawl]
                    await asyncio.gather(*tasks, return_exceptions=True)
                    
                    await asyncio.sleep(self.crawl_delay)
                    
                except Exception as e:
                    print(f"Crawler error: {e}")
                    await asyncio.sleep(30)
    
    def get_next_crawl_batch(self) -> List[dict]:
        """Get next batch of URLs to crawl from the queue."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('''
                SELECT url, priority, source_url, attempts
                FROM crawl_queue
                WHERE next_crawl <= datetime('now')
                  AND attempts < 3
                ORDER BY priority DESC, added_at ASC
                LIMIT ?
            ''', (self.max_concurrent,))
            
            results = cursor.fetchall()
            
            # Update next_crawl time to prevent immediate re-crawling
            if results:
                urls = [row[0] for row in results]
                placeholders = ','.join('?' for _ in urls)
                cursor.execute(f'''
                    UPDATE crawl_queue 
                    SET next_crawl = datetime('now', '+1 hour'),
                        attempts = attempts + 1,
                        last_attempt = datetime('now')
                    WHERE url IN ({placeholders})
                ''', urls)
                conn.commit()
            
            return [{'url': row[0], 'priority': row[1], 'source_url': row[2], 'attempts': row[3]} 
                    for row in results]
            
        finally:
            conn.close()
    
    async def crawl_url(self, url_data: dict):
        """Crawl a single URL and extract content and links."""
        url = url_data['url']
        
        try:
            headers = {
                'User-Agent': random.choice(self.user_agents),
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate',
                'Connection': 'keep-alive',
            }
            
            async with self.session.get(url, headers=headers) as response:
                if response.status != 200:
                    return
                
                content = await response.text()
                content_type = response.headers.get('content-type', '')
                
                if 'text/html' not in content_type:
                    return
                
                # Parse content
                soup = BeautifulSoup(content, 'html.parser')
                
                # Extract page data
                page_data = self.extract_page_data(soup, url, response.status)
                
                # Save/update page
                self.save_page_data(url, page_data)
                
                # Extract and queue new links
                links = self.extract_links(soup, url)
                self.queue_new_links(links, url)
                
                # Download favicon if not exists
                await self.download_favicon(url, soup)
                
        except asyncio.TimeoutError:
            print(f"Timeout crawling {url}")
        except Exception as e:
            print(f"Error crawling {url}: {e}")
    
    def extract_page_data(self, soup: BeautifulSoup, url: str, status_code: int) -> dict:
        """Extract structured data from a web page."""
        title = soup.title.string.strip() if soup.title else ""
        
        # Extract description from meta tags
        description = ""
        meta_desc = soup.find('meta', attrs={'name': 'description'})
        if meta_desc:
            description = meta_desc.get('content', '')
        
        # Extract keywords
        keywords = ""
        meta_keywords = soup.find('meta', attrs={'name': 'keywords'})
        if meta_keywords:
            keywords = meta_keywords.get('content', '')
        
        # Extract main content text
        content_text = self.extract_content_text(soup)
        
        # Generate content hash for deduplication
        content_hash = hashlib.md5(content_text.encode()).hexdigest()
        
        # Extract language
        language = soup.get('lang', '') or self.detect_language(content_text)
        
        return {
            'title': title[:500],  # Limit length
            'description': description[:1000],
            'keywords': keywords[:500],
            'content_text': content_text[:5000],  # Limit content length
            'content_hash': content_hash,
            'language': language,
            'status_code': status_code,
            'domain': extract_domain(url)
        }
    
    def extract_content_text(self, soup: BeautifulSoup) -> str:
        """Extract main content text from the page."""
        # Remove script and style elements
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        # Try to find main content areas
        main_content = soup.find('main') or soup.find('article') or soup.find('div', class_=re.compile(r'content|main|article'))
        
        if main_content:
            text = main_content.get_text()
        else:
            # Fallback to body text
            text = soup.get_text()
        
        # Clean up text
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = ' '.join(chunk for chunk in chunks if chunk)
        
        return text
    
    def detect_language(self, text: str) -> str:
        """Simple language detection (can be enhanced with proper language detection library)."""
        # Basic English detection
        english_words = {'the', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by', 'a', 'an'}
        words = set(text.lower().split()[:50])  # Check first 50 words
        
        english_count = len(words.intersection(english_words))
        if english_count > len(words) * 0.3:
            return 'en'
        
        return 'unknown'
    
    def save_page_data(self, url: str, page_data: dict):
        """Save or update page data in the database."""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            # Check if page exists
            cursor.execute('SELECT id, content_hash FROM pages WHERE url = ?', (url,))
            existing = cursor.fetchone()
            
            current_time = datetime.now().isoformat()
            
            if existing:
                # Update existing page if content changed
                if existing[1] != page_data['content_hash']:
                    cursor.execute('''
                        UPDATE pages SET 
                            title = ?, description = ?, content_text = ?, keywords = ?,
                            content_hash = ?, language = ?, status_code = ?,
                            last_crawled = ?, domain = ?
                        WHERE url = ?
                    ''', (
                        page_data['title'], page_data['description'], page_data['content_text'],
                        page_data['keywords'], page_data['content_hash'], page_data['language'],
                        page_data['status_code'], current_time, page_data['domain'], url
                    ))
            else:
                # Insert new page
                cursor.execute('''
                    INSERT INTO pages (
                        url, title, description, content_text, keywords, 
                        content_hash, language, status_code, last_crawled, domain, priority
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                ''', (
                    url, page_data['title'], page_data['description'], page_data['content_text'],
                    page_data['keywords'], page_data['content_hash'], page_data['language'],
                    page_data['status_code'], current_time, page_data['domain']
                ))
            
            conn.commit()
            
            # Remove from crawl queue
            cursor.execute('DELETE FROM crawl_queue WHERE url = ?', (url,))
            conn.commit()
            
        finally:
            conn.close()
    
    def extract_links(self, soup: BeautifulSoup, base_url: str) -> List[dict]:
        """Extract all links from the page."""
        links = []
        
        for link in soup.find_all('a', href=True):
            href = link['href']
            absolute_url = urljoin(base_url, href)
            
            # Basic URL validation
            if not self.is_valid_url(absolute_url):
                continue
            
            anchor_text = link.get_text(strip=True)[:200]  # Limit anchor text length
            
            links.append({
                'url': absolute_url,
                'anchor_text': anchor_text
            })
        
        return links
    
    def is_valid_url(self, url: str) -> bool:
        """Check if URL is valid for crawling."""
        try:
            parsed = urlparse(url)
            
            # Must have scheme and netloc
            if not parsed.scheme or not parsed.netloc:
                return False
            
            # Only HTTP and HTTPS
            if parsed.scheme not in ['http', 'https']:
                return False
            
            # Skip certain file extensions
            skip_extensions = {'.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', 
                               '.zip', '.rar', '.tar', '.gz', '.mp3', '.mp4', '.avi', 
                               '.jpg', '.jpeg', '.png', '.gif', '.svg', '.ico'}
            
            if any(parsed.path.lower().endswith(ext) for ext in skip_extensions):
                return False
            
            return True
            
        except:
            return False
    
    def queue_new_links(self, links: List[dict], source_url: str):
        """Add new links to the crawl queue."""
        if not links:
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            for link in links:
                url = link['url']
                anchor_text = link['anchor_text']
                
                # Check if URL is already in queue or crawled
                cursor.execute('''
                    SELECT 1 FROM crawl_queue WHERE url = ?
                    UNION ALL
                    SELECT 1 FROM pages WHERE url = ?
                    LIMIT 1
                ''', (url, url))
                
                if cursor.fetchone():
                    continue  # Already exists
                
                # Add to queue with priority based on anchor text and domain
                priority = self.calculate_link_priority(url, anchor_text, source_url)
                
                cursor.execute('''
                    INSERT OR IGNORE INTO crawl_queue (url, priority, source_url, next_crawl)
                    VALUES (?, ?, ?, datetime('now', '+' || ? || ' minutes'))
                ''', (url, priority, source_url, random.randint(1, 60)))
                
                # Also save link relationship
                cursor.execute('''
                    INSERT OR IGNORE INTO links (from_url, to_url, anchor_text)
                    VALUES (?, ?, ?)
                ''', (source_url, url, anchor_text))
            
            conn.commit()
            
        finally:
            conn.close()
    
    def calculate_link_priority(self, url: str, anchor_text: str, source_url: str) -> int:
        """Calculate priority for a discovered link."""
        priority = 0
        
        # Higher priority for home pages
        parsed = urlparse(url)
        if parsed.path in ['', '/']:
            priority += 10
        
        # Higher priority for meaningful anchor text
        if anchor_text and len(anchor_text) > 5:
            priority += 5
        
        # Higher priority for same domain
        source_domain = extract_domain(source_url)
        target_domain = extract_domain(url)
        if source_domain == target_domain:
            priority += 3
        
        # Lower priority for deep URLs
        path_depth = len([p for p in parsed.path.split('/') if p])
        priority -= min(path_depth, 5)
        
        return max(priority, 0)
    
    async def download_favicon(self, url: str, soup: BeautifulSoup):
        """Download and save favicon for the website."""
        try:
            domain = extract_domain(url)
            favicon_id = hashlib.md5(domain.encode()).hexdigest()
            
            # Check if favicon already exists
            favicon_extensions = ['ico', 'png', 'svg', 'jpg', 'webp']
            for ext in favicon_extensions:
                if os.path.exists(os.path.join(FAVICON_DIR, f"{favicon_id}.{ext}")):
                    return  # Already have favicon
            
            # Try to find favicon URL
            favicon_url = None
            
            # Look for favicon link in HTML
            icon_links = soup.find_all('link', rel=lambda x: x and 'icon' in x.lower())
            if icon_links:
                favicon_url = urljoin(url, icon_links[0].get('href'))
            else:
                # Try default favicon.ico
                favicon_url = urljoin(url, '/favicon.ico')
            
            if favicon_url:
                await self.save_favicon(favicon_url, favicon_id, domain)
                
        except Exception as e:
            print(f"Error downloading favicon for {url}: {e}")
    
    async def save_favicon(self, favicon_url: str, favicon_id: str, domain: str):
        """Save favicon file and update database."""
        try:
            async with self.session.get(favicon_url) as response:
                if response.status == 200:
                    content = await response.read()
                    content_type = response.headers.get('content-type', '')
                    
                    # Determine file extension
                    ext = 'ico'
                    if 'png' in content_type:
                        ext = 'png'
                    elif 'svg' in content_type:
                        ext = 'svg'
                    elif 'jpeg' in content_type or 'jpg' in content_type:
                        ext = 'jpg'
                    elif 'webp' in content_type:
                        ext = 'webp'
                    
                    favicon_path = os.path.join(FAVICON_DIR, f"{favicon_id}.{ext}")
                    
                    with open(favicon_path, 'wb') as f:
                        f.write(content)
                    
                    # Update pages with favicon_id
                    conn = get_db_connection()
                    cursor = conn.cursor()
                    try:
                        cursor.execute('''
                            UPDATE pages SET favicon_id = ? WHERE domain = ? AND favicon_id IS NULL
                        ''', (favicon_id, domain))
                        conn.commit()
                    finally:
                        conn.close()
                        
        except Exception as e:
            print(f"Error saving favicon {favicon_url}: {e}")

# Global crawler instance
crawler = InfiniteCrawler()

@app.on_event("startup")
async def startup_event():
    """Start the infinite crawler when the API starts."""
    # Start crawler in background
    asyncio.create_task(crawler.start_crawling())

@app.on_event("shutdown")
async def shutdown_event():
    """Stop the crawler when the API shuts down."""
    crawler.running = False

# Webmaster Console - Domain Verification and Analytics

@app.post("/webmaster/verify")
def verify_domain(domain: str = Query(...), method: str = Query("dns", regex="^(dns|file)$")):
    """Initiate domain verification process."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Generate verification token
        verification_token = hashlib.md5(f"{domain}-{datetime.now().isoformat()}".encode()).hexdigest()[:16]
        
        # Store verification request
        cursor.execute('''
            INSERT OR REPLACE INTO domain_verifications (domain, verification_token, method, status, created_at)
            VALUES (?, ?, ?, 'pending', datetime('now'))
        ''', (domain, verification_token, method))
        conn.commit()
        
        if method == "dns":
            return {
                "domain": domain,
                "verification_method": "dns",
                "verification_token": verification_token,
                "instructions": f"Add a TXT record to your DNS: nova-site-verification={verification_token}"
            }
        else:  # file method
            return {
                "domain": domain,
                "verification_method": "file",
                "verification_token": verification_token,
                "instructions": f"Place a file at https://{domain}/nova-verify.txt containing: {verification_token}"
            }
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.post("/webmaster/verify/confirm")
def confirm_domain_verification(domain: str = Query(...)):
    """Confirm domain verification by checking TXT record or file."""
    import socket
    import requests
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Get pending verification
        cursor.execute('''
            SELECT verification_token, method FROM domain_verifications 
            WHERE domain = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
        ''', (domain,))
        
        result = cursor.fetchone()
        if not result:
            raise HTTPException(status_code=404, detail="No pending verification found for this domain")
        
        verification_token, method = result
        verified = False
        
        if method == "dns":
            try:
                # Check DNS TXT record
                import dns.resolver
                answers = dns.resolver.resolve(domain, 'TXT')
                for answer in answers:
                    txt_content = str(answer).strip('"')
                    if f"nova-site-verification={verification_token}" in txt_content:
                        verified = True
                        break
            except:
                # Fallback without dns library
                verified = False
        
        elif method == "file":
            try:
                # Check verification file
                response = requests.get(f"https://{domain}/nova-verify.txt", timeout=10)
                if response.status_code == 200 and verification_token in response.text:
                    verified = True
            except:
                verified = False
        
        if verified:
            # Update verification status
            cursor.execute('''
                UPDATE domain_verifications 
                SET status = 'verified', verified_at = datetime('now')
                WHERE domain = ? AND verification_token = ?
            ''', (domain, verification_token))
            conn.commit()
            
            return {"domain": domain, "status": "verified", "message": "Domain successfully verified"}
        else:
            return {"domain": domain, "status": "failed", "message": "Verification failed. Please check your DNS record or file."}
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.get("/webmaster/analytics/{domain}")
def get_domain_analytics(domain: str):
    """Get analytics for a verified domain."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Check if domain is verified
        cursor.execute('''
            SELECT 1 FROM domain_verifications 
            WHERE domain = ? AND status = 'verified'
        ''', (domain,))
        
        if not cursor.fetchone():
            raise HTTPException(status_code=403, detail="Domain not verified. Please verify domain ownership first.")
        
        # Get domain statistics
        cursor.execute('''
            SELECT COUNT(*) FROM pages WHERE domain = ?
        ''', (domain,))
        total_pages = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM pages 
            WHERE domain = ? AND last_crawled > datetime('now', '-24 hours')
        ''', (domain,))
        crawled_24h = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM links WHERE to_url LIKE ?
        ''', (f"https://{domain}%",))
        inbound_links = cursor.fetchone()[0]
        
        cursor.execute('''
            SELECT COUNT(*) FROM links WHERE from_url LIKE ?
        ''', (f"https://{domain}%",))
        outbound_links = cursor.fetchone()[0]
        
        # Get top pages by priority/relevance
        cursor.execute('''
            SELECT url, title, priority, page_rank, last_crawled
            FROM pages 
            WHERE domain = ?
            ORDER BY priority DESC, page_rank DESC
            LIMIT 10
        ''', (domain,))
        
        top_pages = []
        for row in cursor.fetchall():
            top_pages.append({
                "url": row[0],
                "title": row[1],
                "priority": row[2],
                "page_rank": row[3],
                "last_crawled": row[4]
            })
        
        return {
            "domain": domain,
            "overview": {
                "total_pages": total_pages,
                "crawled_last_24h": crawled_24h,
                "inbound_links": inbound_links,
                "outbound_links": outbound_links
            },
            "top_pages": top_pages
        }
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.get("/webmaster/domains")
def get_verified_domains():
    """Get list of verified domains for the user.""" 
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            SELECT domain, verified_at FROM domain_verifications 
            WHERE status = 'verified'
            ORDER BY verified_at DESC
        ''')
        
        domains = []
        for row in cursor.fetchall():
            domains.append({
                "domain": row[0],
                "verified_at": row[1]
            })
        
        return {"verified_domains": domains}
        
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

class ClickData(BaseModel):
    url: str

def update_priority(url: str, change: int):
    """Update the priority of a page by a given change."""
    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute('UPDATE pages SET priority = priority + ? WHERE url = ?', (change, url))
        conn.commit()
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()

@app.post("/quality/rateresult/pageclick")
def increase_priority(data: ClickData):
    """Increase the priority of a page by 1."""
    update_priority(data.url, 1)
    return {"detail": "Priority updated"}

@app.post("/quality/rateresult/bad")
def decrease_priority(data: ClickData):
    """Decrease the priority of a page by 2."""
    update_priority(data.url, -2)
    return {"detail": "Thank you for your feedback!"}

@app.post("/quality/rateresult/good")
def increase_priority_by_two(data: ClickData):
    """Increase the priority of a page by 2."""
    update_priority(data.url, 2)
    return {"detail": "Thank you for your feedback!"}

@app.get("/favicon/{favicon_id}")
def get_favicon(favicon_id: str):
    """Retrieve a favicon by its ID."""
    extensions = ['ico', 'png', 'svg', 'jpg', 'webp']
    for ext in extensions:
        favicon_path = os.path.join(FAVICON_DIR, f"{favicon_id}.{ext}")
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)

    raise HTTPException(status_code=404, detail="Favicon not found.")

@app.get("/stats")
def get_stats():
    """Get crawl statistics."""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # Total pages
        cursor.execute("SELECT COUNT(*) FROM pages")
        total_pages = cursor.fetchone()[0]
        
        # Pages crawled in last 24 hours
        cursor.execute(
            "SELECT COUNT(*) FROM pages WHERE last_crawled > datetime('now', '-1 day')"
        )
        crawled_last_24h = cursor.fetchone()[0]
        
        # Pages never crawled
        cursor.execute("SELECT COUNT(*) FROM pages WHERE last_crawled IS NULL")
        never_crawled = cursor.fetchone()[0]
        
        # Oldest crawl
        cursor.execute(
            "SELECT MIN(last_crawled) FROM pages WHERE last_crawled IS NOT NULL"
        )
        oldest_crawl = cursor.fetchone()[0]
        
        # Top domains
        cursor.execute("""
            SELECT 
                substr(url, instr(url, '://') + 3, 
                       case when instr(substr(url, instr(url, '://') + 3), '/') = 0 
                            then length(url) 
                            else instr(substr(url, instr(url, '://') + 3), '/') - 1 
                       end) as domain,
                COUNT(*) as count
            FROM pages
            GROUP BY domain
            ORDER BY count DESC
            LIMIT 10
        """)
        top_domains = [{"domain": row[0], "count": row[1]} for row in cursor.fetchall()]
        
        return {
            "total_pages": total_pages,
            "crawled_last_24h": crawled_last_24h,
            "never_crawled": never_crawled,
            "oldest_crawl": oldest_crawl,
            "top_domains": top_domains
        }
    
    except sqlite3.Error as e:
        raise HTTPException(status_code=500, detail=f"Database error: {e}")
    finally:
        conn.close()