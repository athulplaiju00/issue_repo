import asyncio
import logging
import oracledb
import os
from dotenv import load_dotenv
import httpx
from openai import AsyncOpenAI
from models.issue_queries import connect_to_adw  # your DB connector

# ----------------------------
# CONFIGURATION
# ----------------------------
load_dotenv(override=True)
API_KEY = os.getenv("CHATBOT_API_KEY", "vk_aspire_O4iWo7IG1c3uhtLq")
URL = os.getenv("CHATBOT_API_URL", "https://km-bot.aspiresystems.com/ot/v1/chat/completions")
BASE_URL = URL.rsplit("/chat/completions", 1)[0] if URL else None

client = AsyncOpenAI(
    api_key=API_KEY,
    base_url=BASE_URL,
    http_client=httpx.AsyncClient(verify=False)
)
VECTOR_DIM = 1536
MODEL_NAME = "text-embedding-ada-002"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ----------------------------
# 1️ CREATE VECTOR TABLE
# ----------------------------
def ensure_vector_table(conn):
    sql = f"""
    CREATE TABLE VECTOR_ISSUES (
        ISSUE_ID NUMBER PRIMARY KEY,
        ISSUE_TEXT CLOB,
        EMBEDDING VECTOR({VECTOR_DIM}, FLOAT32),
        MODEL_NAME VARCHAR2(100),
        UPDATED_ON DATE DEFAULT SYSDATE
    )
    """
    with conn.cursor() as cur:
        try:
            cur.execute(sql)
            logger.info(" VECTOR_ISSUES table created successfully.")
        except Exception as e:
            if "ORA-00955" in str(e):
                logger.info("ℹ VECTOR_ISSUES table already exists.")
            else:
                raise

# ----------------------------
# 2️ FETCH ISSUES
# ----------------------------
def fetch_issues(conn, issue_ids: list[int] = None, limit=None):
    sql = """
        SELECT ISSUE_ID, ISSUE_TITLE, ISSUE_DESCRIPTION, AFFECTED_MODULE,
               SUBMODULE, SEVERITY, IMPACT_AREA, ROOT_CAUSE_IDENTIFIED,
               ROOT_CAUSE_DETAILS, RESOLUTION_DETAILS, ORACLE_DOC_REFERENCE,
               ISSUE_SOURCE, ISSUE_RAISED_DATE, ISSUE_REPORTED_BY, STATUS,
               REVIEWER_COMMENTS, TAGS, CREATED_DATE, CREATED_BY,
               UPDATED_DATE, UPDATED_BY, PROJECT_REFERENCE
        FROM ISSUE_TRACKER
        WHERE 1=1
    """
    if not issue_ids:
        # Avoid fetching already vectorized issues to enable resume-on-failure
        sql += """ AND ISSUE_ID NOT IN (SELECT ISSUE_ID FROM VECTOR_ISSUES)"""

    params = {}
    if issue_ids:
        bind_names = [f":id{i}" for i in range(len(issue_ids))]
        sql += f" AND ISSUE_ID IN ({', '.join(bind_names)})"
        params = {f"id{i}": val for i, val in enumerate(issue_ids)}

    if limit:
        sql += f" FETCH FIRST {limit} ROWS ONLY"

    with conn.cursor() as cur:
        if params:
            cur.execute(sql, params)
        else:
            cur.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = []
        for r in cur.fetchall():
            rec = dict(zip(cols, r))
            for k, v in rec.items():
                if isinstance(v, oracledb.LOB):
                    rec[k] = v.read()
            rows.append(rec)
        return rows

# ----------------------------
# 3️ COMBINE COLUMNS INTO ONE TEXT
# ----------------------------
def combine_issue_text(issue_row):
    return " | ".join(
        f"{k}: {str(v).strip()}"
        for k, v in issue_row.items()
        if v and str(v).strip().lower() not in ("none", "null", "nat")
    )

# ----------------------------
# 4️ GENERATE EMBEDDINGS (ASYNC)
# ----------------------------
async def get_embedding_async(text: str):
    """Generate OpenAI embedding asynchronously with retry logic."""
    for attempt in range(4):  # 4 attempts total (1 initial + 3 retries)
        try:
            response = await client.embeddings.create(
                model=MODEL_NAME,
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            if attempt == 3:
                logger.error(f"Embedding generation failed after all attempts: {e}")
                return None
            wait_time = 2 ** attempt
            logger.warning(f"Embedding generation failed (attempt {attempt+1}/4): {e}. Retrying in {wait_time}s...")
            await asyncio.sleep(wait_time)

async def get_embeddings_parallel(texts):
    sem = asyncio.Semaphore(2)  # Limit to 2 concurrent requests to prevent overloading the proxy

    async def sem_get_embedding(text):
        async with sem:
            await asyncio.sleep(0.05)  # Slight delay to pace requests
            return await get_embedding_async(text)

    tasks = [sem_get_embedding(t) for t in texts]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [r if isinstance(r, list) else None for r in results]

# ----------------------------
# 5️ UPSERT INTO VECTOR_ISSUES
# ----------------------------
def upsert_vector_issue(conn, issue_id, issue_text, embedding):
    """Insert or update a row in VECTOR_ISSUES with a true VECTOR type."""
    if not embedding:
        return

    # Prepare proper VECTOR literal
    embedding_str = "[" + ", ".join(f"{float(x):.6f}" for x in embedding) + "]"

    sql = """
        MERGE INTO VECTOR_ISSUES v
        USING DUAL
        ON (v.ISSUE_ID = :id)
        WHEN MATCHED THEN
            UPDATE SET 
                v.ISSUE_TEXT = :text,
                v.EMBEDDING = TO_VECTOR(:vec),
                v.MODEL_NAME = :model,
                v.UPDATED_ON = SYSDATE
        WHEN NOT MATCHED THEN
            INSERT (ISSUE_ID, ISSUE_TEXT, EMBEDDING, MODEL_NAME)
            VALUES (:id, :text, TO_VECTOR(:vec), :model)
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "id": issue_id,
            "text": issue_text,
            "vec": embedding_str,
            "model": MODEL_NAME
        })

# ----------------------------
# 6️ MAIN PROCESS: STORE ISSUE VECTORS
# ----------------------------
async def store_issue_vectors(issue_ids: list[int] = None, limit=None):
    """Fetch issues, create embeddings, and store into VECTOR_ISSUES."""
    conn = connect_to_adw()
    ensure_vector_table(conn)

    issues = fetch_issues(conn, issue_ids, limit)
    logger.info(f" Retrieved {len(issues)} issues from ISSUE_TRACKER")

    texts = [combine_issue_text(i) for i in issues]
    embeddings = await get_embeddings_parallel(texts)

    count = 0
    for issue, emb in zip(issues, embeddings):
        if emb:
            try:
                upsert_vector_issue(conn, issue["ISSUE_ID"], combine_issue_text(issue), emb)
                conn.commit()  # Commit immediately to preserve progress
                count += 1
            except Exception as e:
                logger.error(f"Failed to save vector for issue {issue['ISSUE_ID']}: {e}")

    conn.close()
    logger.info(f" Stored {count} issue embeddings in VECTOR_ISSUES.")
    return count

# ----------------------------
# 7️ SEARCH USING COSINE SIMILARITY (Oracle 23ai)
# ----------------------------
def search_issue_vectors(conn, query_embedding, top_k=5):
    """Search for similar issues using cosine similarity."""

    query_str = "[" + ", ".join(f"{x:.6f}" for x in query_embedding) + "]"

    sql = f"""
        SELECT 
            ISSUE_ID,
            ISSUE_TEXT,
            (
                1 - VECTOR_DISTANCE(
                    EMBEDDING,
                    TO_VECTOR(:vec),
                    COSINE
                )
            ) AS similarity
        FROM VECTOR_ISSUES
        ORDER BY similarity DESC
        FETCH FIRST {top_k} ROWS ONLY
    """

    results = []

    with conn.cursor() as cur:
        cur.execute(sql, [query_str])

        for issue_id, issue_text, similarity in cur.fetchall():
            if hasattr(issue_text, "read"):
                issue_text = issue_text.read()

            results.append({
                "issue_id": issue_id,
                "text": str(issue_text),
                "similarity": round(float(similarity), 4)
            })

    return results



def run_store_issue_vectors_background(issue_ids: list[int] = None):
    """Runs async store_issue_vectors() in a safe background thread."""
    try:
        logger.info(f" Background embedding task started for IDs: {issue_ids}...")
        asyncio.run(store_issue_vectors(issue_ids, limit=None))
        logger.info(" Background embedding task completed successfully.")
    except Exception as e:
        logger.error(f" Background embedding error: {e}")


# ----------------------------
# 8️ TEST RUNNER
# ----------------------------
if __name__ == "__main__":
    asyncio.run(store_issue_vectors(limit=5))
