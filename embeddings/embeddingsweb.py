import re
import ssl
import certifi
import asyncio
import logging
import requests
import urllib3
import httpx
import os
from dotenv import load_dotenv

from bs4 import BeautifulSoup
from openai import AsyncOpenAI
from models.issue_queries import connect_to_metadata

# ---------------------------------------
# CONFIG
# ---------------------------------------
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ssl_context = ssl.create_default_context(cafile=certifi.where())
httpx._default_ssl_context = ssl_context

load_dotenv(override=True)
API_KEY = os.getenv("CHATBOT_API_KEY")
URL = os.getenv("CHATBOT_API_URL", "https://km-bot.aspiresystems.com/ot/v1/chat/completions")
BASE_URL = URL.rsplit("/chat/completions", 1)[0] if URL else None

client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.AsyncClient(verify=False)
)

MODEL_NAME = "text-embedding-ada-002"
VECTOR_DIM = 1536

logger = logging.getLogger("web_embeddings")
logging.basicConfig(level=logging.INFO)

# ---------------------------------------
# EMBEDDING FUNCTION (LOCAL & GUARANTEED)
# ---------------------------------------
async def get_embedding_async(text: str):
    """
    ALWAYS returns 3072-dimension embedding
    """
    try:
        response = await client.embeddings.create(
            model=MODEL_NAME,
            input=text
        )
        embedding = response.data[0].embedding

        # 🔒 HARD SAFETY CHECK
        if len(embedding) != VECTOR_DIM:
            raise ValueError(
                f"Embedding dim mismatch: {len(embedding)} != {VECTOR_DIM}"
            )

        return embedding

    except Exception as e:
        logger.error(f"Embedding generation failed: {e}")
        return None

# ---------------------------------------
# TABLE CREATION
# ---------------------------------------
def ensure_web_vector_table(conn):
    sql = f"""
    CREATE TABLE WEB_SCRAPED_VECTORS (
        DOC_ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        PAGE_TITLE VARCHAR2(300),
        SOURCE_URL VARCHAR2(500),
        CHUNK_TEXT CLOB,
        EMBEDDING VECTOR({VECTOR_DIM}, FLOAT32),
        MODEL_NAME VARCHAR2(100),
        CREATED_ON DATE DEFAULT SYSDATE
    )
    """
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            conn.commit()
            logger.info("WEB_SCRAPED_VECTORS table created")
        except Exception as e:
            if "ORA-00955" in str(e):
                logger.info("WEB_SCRAPED_VECTORS table already exists")
            else:
                raise

# ---------------------------------------
# WEB SCRAPING
# ---------------------------------------
def extract_web_page(url: str):
    try:
        resp = requests.get(url, timeout=20, verify=False)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        for tag in soup(["script", "style", "noscript", "header", "footer"]):
            tag.decompose()

        text = re.sub(r"\s+", " ", " ".join(soup.stripped_strings))
        title = soup.title.string.strip() if soup.title else "Unknown"

        return {
            "title": title,
            "text": text,
            "url": url
        }

    except Exception as e:
        logger.error(f"Web scrape failed: {e}")
        return None

# ---------------------------------------
# CHUNKING
# ---------------------------------------
def split_text_into_chunks(text: str, max_len=150):
    words = text.split()
    return [
        " ".join(words[i:i + max_len])
        for i in range(0, len(words), max_len)
    ]

# ---------------------------------------
# STORE EMBEDDINGS (STRING VECTOR)
# ---------------------------------------
def store_web_embeddings(conn, title, url, chunks, embeddings):
    with conn.cursor() as cur:
        for chunk, emb in zip(chunks, embeddings):
            if not emb:
                continue

            # STRING VECTOR (Oracle-safe)
            emb_str = "[" + ", ".join(f"{x:.6f}" for x in emb) + "]"

            cur.execute("""
                INSERT INTO WEB_SCRAPED_VECTORS
                (PAGE_TITLE, SOURCE_URL, CHUNK_TEXT, EMBEDDING, MODEL_NAME)
                VALUES (:1, :2, :3, :4, :5)
            """, [
                title,
                url,
                chunk,
                emb_str,
                MODEL_NAME
            ])
    conn.commit()




def is_url_already_scraped(conn, url: str) -> bool:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT 1
            FROM WEB_SCRAPED_VECTORS
            WHERE SOURCE_URL = :1
            FETCH FIRST 1 ROWS ONLY
        """, [url])
        return cur.fetchone() is not None

# ---------------------------------------
# MAIN ASYNC SERVICE
# ---------------------------------------
async def embed_web_url(url: str):
    conn = connect_to_metadata()
    try:
        ensure_web_vector_table(conn)

        #  CHECK IF URL ALREADY SCRAPED
        if is_url_already_scraped(conn, url):
            return [
                {
                    "status": "skipped",
                    "url": url,
                    "message": "This URL is already scraped. Try with a different URL."
                }
            ]

        # Continue scraping
        page = extract_web_page(url)
        if not page or not page["text"]:
            return [
                {
                    "status": "error",
                    "url": url,
                    "message": "No content extracted from URL"
                }
            ]

        chunks = split_text_into_chunks(page["text"], max_len=150)
        logger.info(f"Splitting into {len(chunks)} chunks")

        embeddings = await asyncio.gather(
            *(get_embedding_async(c) for c in chunks)
        )

        valid_embeddings = [e for e in embeddings if e is not None]
        if not valid_embeddings:
            return [
                {
                    "status": "error",
                    "url": url,
                    "message": "Embedding generation failed"
                }
            ]

        store_web_embeddings(
            conn,
            page["title"],
            page["url"],
            chunks,
            valid_embeddings
        )

        return [
            {
                "status": "success",
                "url": url,
                "page_title": page["title"],
                "chunks_stored": len(valid_embeddings)
            }
        ]

    finally:
        conn.close()

# ---------------------------------------
# SYNC WRAPPER (OPTIONAL)
# ---------------------------------------
def embed_web_url_sync(url: str):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(embed_web_url(url))
    finally:
        loop.close()




#scrap all urls

from urllib.parse import urljoin, urlparse

def extract_links(base_url: str, html: str, same_domain_only=True):
    soup = BeautifulSoup(html, "html.parser")
    links = set()

    base_domain = urlparse(base_url).netloc

    for a in soup.find_all("a", href=True):
        href = a.get("href").strip()
        if href.startswith("#"):
            continue

        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.scheme not in ("http", "https"):
            continue

        if same_domain_only and parsed.netloc != base_domain:
            continue

        links.add(full_url)

    return list(links)




import asyncio
import requests
import re
from bs4 import BeautifulSoup

async def scrape_page_and_links(
    url: str,
    max_links: int = 20,
    same_domain_only: bool = True
):
    # 1️⃣ Root page (static fetch is OK)
    resp = requests.get(url, timeout=20, verify=False)
    resp.raise_for_status()

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(["script", "style", "noscript", "header", "footer"]):
        tag.decompose()

    root_text = re.sub(r"\s+", " ", " ".join(soup.stripped_strings))

    # 2️⃣ JS-rendered links (ASYNC)
    links = await fetch_hyperlinks_with_js(
        url,
        same_domain_only=same_domain_only,
        max_links=max_links
    )

    results = [{
        "url": url,
        "is_root": True,
        "text_length": len(root_text),
        "preview": root_text[:500]
    }]

    # 3️⃣ Fetch child pages (controlled)
    for link in links:
        try:
            r = requests.get(link, timeout=15, verify=False)
            r.raise_for_status()

            s = BeautifulSoup(r.text, "html.parser")
            for tag in s(["script", "style", "noscript", "header", "footer"]):
                tag.decompose()

            text = re.sub(r"\s+", " ", " ".join(s.stripped_strings))
            if not text:
                continue

            results.append({
                "url": link,
                "is_root": False,
                "text_length": len(text),
                "preview": text[:500]
            })

        except Exception:
            continue

    return {
        "root_url": url,
        "total_pages_scraped": len(results),
        "pages": results
    }


from playwright.async_api import async_playwright
from urllib.parse import urlparse

async def fetch_hyperlinks_with_js(
    url: str,
    same_domain_only: bool = True,
    max_links: int = 200
):
    links = set()
    base_domain = urlparse(url).netloc

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()

        # Avoid infinite waits (Oracle Docs)
        await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(3000)

        hrefs = await page.eval_on_selector_all(
            "a[href]",
            "elements => elements.map(el => el.href)"
        )

        await browser.close()

    for link in hrefs:
        parsed = urlparse(link)
        if parsed.scheme not in ("http", "https"):
            continue
        if same_domain_only and parsed.netloc != base_domain:
            continue
        links.add(link)

        if len(links) >= max_links:
            break

    return list(links)








# ---------------------------------------
# SEARCH WEB-SCRAPED CONTENT
# ---------------------------------------
async def search_web_scraped(
    query: str,
    top_k: int = 5,
    full_page: bool = False
):
    conn = connect_to_metadata()
    try:
        # 1️ Generate embedding for query
        emb = await get_embedding_async(query)
        if not emb:
            return {"error": "Failed to generate embedding"}

        # Convert embedding to Oracle VECTOR literal
        emb_str = "[" + ", ".join(f"{x:.6f}" for x in emb) + "]"

        # 2️ Vector search SQL
        sql = f"""
            SELECT PAGE_TITLE,
                   SOURCE_URL,
                   CHUNK_TEXT,
                   (1 - VECTOR_DISTANCE(EMBEDDING, TO_VECTOR(:1))) AS SIMILARITY
            FROM WEB_SCRAPED_VECTORS
            ORDER BY SIMILARITY DESC
            FETCH FIRST {top_k} ROWS ONLY
        """

        with conn.cursor() as cur:
            cur.execute(sql, [emb_str])
            rows = cur.fetchall()

            if not rows:
                return {"query": query, "results": [], "count": 0}

            # 3️ If full page requested, return all chunks for top URL
            if full_page:
                top_url = rows[0][1]
                cur.execute(
                    "SELECT CHUNK_TEXT FROM WEB_SCRAPED_VECTORS WHERE SOURCE_URL = :1",
                    [top_url]
                )
                full_text = " ".join(
                    r[0].read() if hasattr(r[0], "read") else r[0]
                    for r in cur.fetchall()
                )
                return {
                    "query": query,
                    "source_url": top_url,
                    "full_text": full_text,
                    "chunk_count": len(full_text.split())
                }

            # 4️ Normal result list
            results = []
            for title, url, chunk, sim in rows:
                if hasattr(chunk, "read"):
                    chunk = chunk.read()

                results.append({
                    "page_title": title,
                    "source_url": url,
                    "content_preview": chunk[:500].replace("\n", " "),
                    "full_content": chunk,
                    "similarity": round(float(sim), 4)
                })

        return {
            "query": query,
            "results": results,
            "count": len(results)
        }

    finally:
        conn.close()


def searxng_search(query: str, max_results: int = 4) -> list[dict]:
    """
    Search the public web through a SearXNG instance (self-hosted metasearch, no API key).
    Returns a list of {"title", "url", "text"} dicts.
    Requires SEARXNG_URL in .env, e.g. https://searx.example.org
    Optional: SEARXNG_VERIFY_SSL=false for an instance with an untrusted certificate.
    The instance must allow the JSON output format (search.formats includes "json").
    """
    from dotenv import load_dotenv
    load_dotenv(override=True)
    base_url = (os.getenv("SEARXNG_URL") or "").rstrip("/")
    if not base_url:
        logger.warning("SearXNG search not configured: set SEARXNG_URL in .env")
        return []
    verify_ssl = os.getenv("SEARXNG_VERIFY_SSL", "true").strip().lower() != "false"
    try:
        resp = requests.get(
            f"{base_url}/search",
            params={"q": query, "format": "json", "language": "en", "safesearch": 1},
            headers={"User-Agent": "Mozilla/5.0 (compatible; IssueRepoBot/1.0)"},
            timeout=15,
            verify=verify_ssl,
        )
        if resp.status_code != 200:
            logger.warning(f"SearXNG search returned HTTP {resp.status_code}")
            return []
        hits = resp.json().get("results", [])[:max_results]
    except Exception as e:
        logger.error(f"SearXNG search error: {e}")
        return []

    results = []
    for hit in hits:
        url = hit.get("url")
        if not url:
            continue
        # SearXNG returns only snippets, so try to read the full page first.
        text, title = "", hit.get("title", "")
        try:
            page = extract_web_page(url)
            if page and page.get("text"):
                text = page["text"]
                title = page.get("title") or title
        except Exception as e:
            logger.error(f"Failed to extract page {url}: {e}")
        if not text:
            text = hit.get("content", "")
        if not text:
            continue
        words = text.split()
        results.append({"title": title, "url": url, "text": " ".join(words[:1000])})
    return results


def dynamic_web_search_and_extract(query: str, max_results: int = 2) -> list[dict]:
    """
    Search the public web through SearXNG and return the top results' text.
    Returns [] if SearXNG is not configured or fails.
    """
    from urllib.parse import urlparse, parse_qs, quote_plus, unquote
    
    # Optimize search query using LLM to fix typos and translate terms for web search
    try:
        from services.ai_service import ask_ai
        rewrite_prompt = (
            "You are a search query optimizer for Oracle documentation.\n"
            "Your task is to rewrite the user's query to make it highly effective for web search.\n"
            "Rules:\n"
            "1. Fix any typos or run-on words (e.g., 'documentin' -> 'add document', 'oraclle' -> 'Oracle').\n"
            "2. Translate informal terms to standard Oracle terms (e.g., 'agentic ai studio' -> 'AI Agent Studio').\n"
            "3. Keep the core meaning of the query.\n"
            "4. Avoid adding the generic brand keyword 'Oracle' to search keywords unless it is part of a specific product name (e.g., use 'AI Agent Studio' instead of 'Oracle AI Agent Studio'), as it biases search engine results toward general release notes pages.\n"
            "5. Respond with ONLY the optimized search keywords/phrase. Do NOT include any explanations, markdown, or punctuation.\n\n"
            f"User query: {query}"
        )
        optimized_query = ask_ai(rewrite_prompt).strip()
        logger.info(f"Optimized search query from '{query}' to '{optimized_query}'")
        search_query = optimized_query
    except Exception as e:
        logger.error(f"Failed to optimize search query: {e}")
        search_query = query
    
    return searxng_search(search_query, max_results)
