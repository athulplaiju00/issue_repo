import os
import io
import re
import fitz  # PyMuPDF
import pandas as pd
import requests
import urllib3
import asyncio
import logging
import ssl
import certifi
import httpx
import docx  # for .docx files
from urllib.parse import urlsplit, urlunsplit
from openai import AsyncOpenAI
from models.issue_queries import connect_to_adw
from embeddings.embeddings import get_embedding_async
import json
from dotenv import load_dotenv

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ---------------------------------------
# CONFIGURATION
# ---------------------------------------
ssl_context = ssl.create_default_context(cafile=certifi.where())
httpx._default_ssl_context = ssl_context

load_dotenv(override=True)
API_KEY = os.getenv("CHATBOT_API_KEY", "vk_aspire_O4iWo7IG1c3uhtLq")
URL = os.getenv("CHATBOT_API_URL", "https://km-bot.aspiresystems.com/ot/v1/chat/completions")
BASE_URL = URL.rsplit("/chat/completions", 1)[0] if URL else None

client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.AsyncClient(verify=False)
)

VECTOR_DIM = 1536  # for text-embedding-ada-002
MODEL_NAME = "text-embedding-ada-002"

logger = logging.getLogger("embeddings")
logging.basicConfig(level=logging.INFO)

# Oracle Object Storage Pre-Auth URL
PAR_BASE = "https://objectstorage.ap-hyderabad-1.oraclecloud.com/n/axir6m3xzsof/b/Knowledge_repo/o/"
PAR_TOKEN = "Y6s7ot9_Rjv6VqBJ84xRKnUlnCecATSPt6Velbw8JLqf3ZaU0KhCQ1kfQLwiJ7b5"


def ensure_vector_table(conn):
    sql = f"""
    CREATE TABLE VECTOR_DOCUMENTS (
        DOC_ID NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
        FILE_NAME VARCHAR2(255),
        FILE_TYPE VARCHAR2(50),
        CHUNK_TEXT CLOB,
        EMBEDDING VECTOR({VECTOR_DIM}, FLOAT32),
        MODEL_NAME VARCHAR2(100),
        SOURCE_URL VARCHAR2(500),
        CREATED_ON DATE DEFAULT SYSDATE
    )
    """
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
        except Exception as e:
            if "ORA-00955" not in str(e):
                raise


def file_exists_in_db(conn, file_name):
    sql = """
        SELECT 1
        FROM VECTOR_DOCUMENTS
        WHERE FILE_NAME = :file_name
        FETCH FIRST 1 ROWS ONLY
    """
    with conn.cursor() as cur:
        cur.execute(sql, {"file_name": file_name})
        return cur.fetchone() is not None


def extract_object_storage(prefix="uploads/"):
    conn = connect_to_adw()
    results = []

    list_url = f"{PAR_BASE}?p={PAR_TOKEN}&prefix={prefix}"
    response = requests.get(list_url, verify=False, timeout=30)
    if response.status_code != 200:
        return []

    objects = response.json().get("objects", [])

    for obj in objects:
        name = obj.get("name")
        if not name or not name.startswith(prefix):
            continue

        # Proper skip before extraction
        if file_exists_in_db(conn, name):
            logger.info(f"Already vectorized, skipping extraction: {name}")
            continue

        file_url = f"{PAR_BASE}{name}?p={PAR_TOKEN}"
        file_resp = requests.get(file_url, verify=False, timeout=60)
        if file_resp.status_code != 200:
            continue

        file_bytes = file_resp.content
        file_ext = os.path.splitext(name)[1].lower()
        content_type = file_resp.headers.get("Content-Type", "").lower()

        text = None
        file_type = "unknown"

        if "pdf" in content_type or file_ext == ".pdf":
            doc = fitz.open(stream=io.BytesIO(file_bytes), filetype="pdf")
            text = "".join(page.get_text() for page in doc)
            file_type = "pdf"

        elif "word" in content_type or file_ext == ".docx":
            document = docx.Document(io.BytesIO(file_bytes))
            text = "\n".join(p.text for p in document.paragraphs)
            file_type = "docx"

        elif "excel" in content_type or file_ext in [".xls", ".xlsx"]:
            df = pd.read_excel(io.BytesIO(file_bytes))
            text = df.to_string(index=False)
            file_type = "excel"

        elif "json" in content_type or file_ext == ".json":
            text = json.dumps(
                json.loads(file_bytes.decode("utf-8", errors="ignore")),
                indent=2
            )
            file_type = "json"

        elif "csv" in content_type or file_ext == ".csv":
            df = pd.read_csv(io.BytesIO(file_bytes))
            text = df.to_string(index=False)
            file_type = "csv"

        elif "text" in content_type or file_ext == ".txt":
            text = file_bytes.decode("utf-8", errors="ignore")
            file_type = "txt"

        if text:
            results.append({
                "file": name,
                "type": file_type,
                "data": text,
                "url": file_url
            })

    conn.close()
    return results


def split_text_into_chunks(text, max_len=150):
    words = text.split()
    return [" ".join(words[i:i + max_len]) for i in range(0, len(words), max_len)]


def store_document_embeddings(conn, file_name, file_type, source_url, chunks, embeddings):
    with conn.cursor() as cur:
        for chunk, emb in zip(chunks, embeddings):
            emb_str = "[" + ", ".join(f"{x:.6f}" for x in emb) + "]"
            cur.execute("""
                INSERT INTO VECTOR_DOCUMENTS
                (FILE_NAME, FILE_TYPE, CHUNK_TEXT, EMBEDDING, MODEL_NAME, SOURCE_URL)
                VALUES (:1, :2, :3, :4, :5, :6)
            """, [file_name, file_type, chunk, emb_str, MODEL_NAME, source_url])
    conn.commit()


async def embed_all_documents(prefix="uploads/"):
    conn = connect_to_adw()
    try:
        ensure_vector_table(conn)

        data = extract_object_storage(prefix)
        if not data:
            logger.info("No new files to vectorize")
            return

        for item in data:
            file_name = item["file"]
            file_type = item["type"]
            text = item["data"]
            url = item["url"]

            if file_exists_in_db(conn, file_name):
                logger.info(f"Already vectorized, skipping embedding: {file_name}")
                continue

            chunks = split_text_into_chunks(text)
            embeddings = await asyncio.gather(
                *(get_embedding_async(c) for c in chunks)
            )

            store_document_embeddings(
                conn, file_name, file_type, url, chunks, embeddings
            )

            logger.info(f"Vectorization completed for {file_name}")

    finally:
        await client.close()
        conn.close()


def run_embed_all_documents_background():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(embed_all_documents())
    loop.close()

# ---------------------------------------
# 6️ SEARCH DOCUMENTS
# ---------------------------------------
async def search_documents(query: str, top_k: int = 5, full_doc: bool = False):
    conn = connect_to_adw()
    try:
        emb = await get_embedding_async(query)
        if not emb:
            return {"error": "Failed to generate embedding"}

        emb_str = "[" + ", ".join(f"{x:.6f}" for x in emb) + "]"

        sql = f"""
            SELECT FILE_NAME, FILE_TYPE, SOURCE_URL, CHUNK_TEXT,
                   (1 - VECTOR_DISTANCE(EMBEDDING, TO_VECTOR(:1))) AS SIMILARITY
            FROM VECTOR_DOCUMENTS
            ORDER BY SIMILARITY DESC
            FETCH FIRST {top_k} ROWS ONLY
        """

        with conn.cursor() as cur:
            cur.execute(sql, [emb_str])
            rows = cur.fetchall()

            if not rows:
                return {"query": query, "results": [], "count": 0}

            top_file = rows[0][0]

            if full_doc:
                cur.execute("SELECT CHUNK_TEXT FROM VECTOR_DOCUMENTS WHERE FILE_NAME = :1", [top_file])
                full_text = " ".join([r[0] for r in cur.fetchall()])
                return {
                    "query": query,
                    "file_name": top_file,
                    "full_text": full_text,
                    "chunk_count": len(full_text.split())
                }

            results = []
            for name, ftype, url, chunk, sim in rows:
                if hasattr(chunk, "read"):
                    chunk = chunk.read()
                results.append({
                    "file_name": name,
                    "file_type": ftype,
                    "source_url": url,
                    "content_preview": chunk[:500].replace("\n", " "),
                    "full_content": chunk,
                    "similarity": round(float(sim), 4)
                })
        return {"query": query, "results": results, "count": len(results)}

    finally:
        conn.close()






