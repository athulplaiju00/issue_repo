
from fastapi import APIRouter, Body, Path,Query
import requests

import asyncio
import pandas as pd
import json
import logging
from embeddings.embeddingsweb import embed_web_url,fetch_hyperlinks_with_js,search_web_scraped

import logging
from pydantic import BaseModel
from fastapi import Request
from embeddings.embeddings import (
    store_issue_vectors,
    search_issue_vectors,
    get_embedding_async,
)
from services.fusion_service import (
    load_history,
    create_new_session,
    append_message
)
from embeddings.embeddingsfiles import embed_all_documents,search_documents
from fastapi import FastAPI, HTTPException
from models.issue_queries import connect_to_adw
import sys, os
from dotenv import load_dotenv

sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
routeremb = APIRouter(tags=["Embeddings"])

load_dotenv(override=True)
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

from services.ai_service import ask_ai

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ENDPOINT: Generate and Store Embeddings
# ----------------------------
@routeremb.post("/store_vectors")
async def store_vectors(limit: int = None):
    """
    Fetch all issues from ISSUE_TRACKER, create embeddings, and store them in VECTOR_ISSUES.
    """
    try:
        logger.info(" Starting embedding generation and storage process...")
        count = await store_issue_vectors(limit)
        return {
            "status": "success",
            "message": f"Stored {count} issue embeddings in VECTOR_ISSUES table.",
        }
    except Exception as e:
        logger.error(f"Error during store_vectors: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    



# ENDPOINT: Search for Similar Issues in table

@routeremb.get("/search_issues")
async def search_issues(
    query: str = Query(..., description="Search text or question"),
    top_k: int = Query(6, description="Number of top results to return")
):
    """
    Search semantically similar issues using Oracle 23AI cosine similarity.
    Example:
        /search_issues?query=invoice%20error&top_k=5
    """
    try:
        # Step 1️: Generate embedding for the query
        logger.info(f"Generating embedding for query: {query}")
        query_embedding = await get_embedding_async(query)
        if not query_embedding:
            raise HTTPException(
                status_code=400,
                detail="Failed to generate embedding for the query.",
            )

        # Step 2️: Connect to Oracle
        conn = connect_to_adw()

        # Step 3️: Perform vector search
        results = search_issue_vectors(conn, query_embedding, top_k)
        conn.close()

        if not results:
            return {
                "query": query,
                "message": "No similar issues found.",
                "matches": [],
            }

        return {
            "query": query,
            "count": len(results),
            "matches": results,
        }

    except Exception as e:
        logger.error(f"Error during search: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    


#files
@routeremb.post("/embed_all")
async def embed_all_files():
    await embed_all_documents()
    return {"status": "success"}

# @routeremb.get("/search_files")
# async def search_files(query: str):
#     return await search_documents(query)

from fastapi import Query, HTTPException

# @routeremb.get("/search_webscrap")
# async def search_webscrap(
#     query: str = Query(..., description="Search text or question"),
#     top_k: int = Query(5, description="Number of top results"),
#     full_page: bool = Query(False, description="Return full page content")
# ):
#     try:
#         return await search_web_scraped(
#             query=query,
#             top_k=top_k,
#             full_page=full_page
#         )
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))

@routeremb.get("/search_all")
async def search_all(
    query: str = Query(..., description="Search text or question"),
    top_k: int = Query(6, description="Total number of results to return after merging")
):
    try:
        logger.info(f"Generating embedding for query: {query}")

        # 1️⃣ Generate query embedding once
        query_embedding = await get_embedding_async(query)
        if not query_embedding:
            raise HTTPException(status_code=400, detail="Failed to generate embedding")

        # -----------------------------
        # 2️⃣ SEARCH ISSUES
        # -----------------------------
        conn = connect_to_adw()
        issue_results = search_issue_vectors(conn, query_embedding, top_k=top_k)
        conn.close()

        # -----------------------------
        # 3️⃣ NORMALIZATION FUNCTION
        # -----------------------------
        def normalize(results, key="similarity"):
            if not results:
                return results

            sims = [r.get(key, 0) for r in results]
            min_sim, max_sim = min(sims), max(sims)

            for r in results:
                if max_sim == min_sim:
                    r["norm_sim"] = 1.0
                else:
                    r["norm_sim"] = (r[key] - min_sim) / (max_sim - min_sim)

            return results

        issue_results = normalize(issue_results)

        # -----------------------------
        # 4️⃣ MERGE RESULTS
        # -----------------------------
        merged_results = []

        # Issues
        for r in issue_results:
            merged_results.append({
                "source": "issue",
                "id": r["issue_id"],
                "title": r.get("text", "")[:80],
                "text": r.get("text"),
                "similarity": r["norm_sim"],
                "original_similarity": r["similarity"]
            })

        # -----------------------------
        # 5️⃣ GLOBAL SORT + TRUNCATE
        # -----------------------------
        merged_results.sort(key=lambda x: x["similarity"], reverse=True)
        final_results = merged_results[:top_k]

        return {
            "query": query,
            "count": len(final_results),
            "matches": final_results
        }

    except Exception as e:
        logger.error(f"Error during unified search: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# @routeremb.get("/search_all")
# async def search_all(
#     query: str = Query(..., description="Search text or question"),
#     top_k: int = Query(15, description="Total number of results to return after merging")
# ):
#     try:
#         logger.info(f"Generating embedding for query: {query}")

#         # 1️⃣ Generate query embedding once
#         query_embedding = await get_embedding_async(query)
#         if not query_embedding:
#             raise HTTPException(status_code=400, detail="Failed to generate embedding")

#         # -----------------------------
#         # 2️⃣ SEARCH ISSUES (TOP 5)
#         # -----------------------------
#         conn = connect_to_adw()
#         issue_results = search_issue_vectors(conn, query_embedding, top_k=5)
#         conn.close()

#         # -----------------------------
#         # 3️⃣ SEARCH FILES (TOP 5)
#         # -----------------------------
#         file_results_raw = await search_documents(query, top_k=5)
#         file_results = file_results_raw.get("results", [])

#         # -----------------------------
#         # 4️⃣ SEARCH WEB SCRAP (TOP 5)  ✅ NEW
#         # -----------------------------
#         web_results_raw = await search_web_scraped(
#             query=query,
#             top_k=5,
#             full_page=False
#         )
#         web_results = web_results_raw.get("results", [])

#         # -----------------------------
#         # 5️⃣ NORMALIZATION FUNCTION
#         # -----------------------------
#         def normalize(results, key="similarity"):
#             if not results:
#                 return results

#             sims = [r.get(key, 0) for r in results]
#             min_sim, max_sim = min(sims), max(sims)

#             for r in results:
#                 if max_sim == min_sim:
#                     r["norm_sim"] = 1.0
#                 else:
#                     r["norm_sim"] = (r[key] - min_sim) / (max_sim - min_sim)

#             return results

#         issue_results = normalize(issue_results)
#         file_results = normalize(file_results)
#         web_results = normalize(web_results)

#         # -----------------------------
#         # 6️⃣ MERGE RESULTS
#         # -----------------------------
#         merged_results = []

#         # Issues
#         for r in issue_results:
#             merged_results.append({
#                 "source": "issue",
#                 "id": r["issue_id"],
#                 "title": r.get("text", "")[:80],
#                 "text": r.get("text"),
#                 "similarity": r["norm_sim"],
#                 "original_similarity": r["similarity"]
#             })

#         # Files
#         for r in file_results:
#             merged_results.append({
#                 "source": "file",
#                 "id": r.get("file_name"),
#                 "title": r.get("content_preview", "")[:80],
#                 "text": r.get("full_content"),
#                 "similarity": r["norm_sim"],
#                 "original_similarity": r["similarity"],
#                 "file_url": r.get("source_url"),
#                 "file_type": r.get("file_type")
#             })

#         # Web Scraped  ✅ NEW
#         for r in web_results:
#             merged_results.append({
#                 "source": "web",
#                 "id": r.get("source_url"),
#                 "title": r.get("page_title"),
#                 "text": r.get("full_content"),
#                 "similarity": r["norm_sim"],
#                 "original_similarity": r["similarity"],
#                 "source_url": r.get("source_url")
#             })

#         # -----------------------------
#         # 7️⃣ GLOBAL SORT + TRUNCATE
#         # -----------------------------
#         merged_results.sort(key=lambda x: x["similarity"], reverse=True)
#         final_results = merged_results[:top_k]

#         return {
#             "query": query,
#             "count": len(final_results),
#             "matches": final_results
#         }

#     except Exception as e:
#         logger.error(f"Error during unified search: {e}")
#         raise HTTPException(status_code=500, detail=str(e))





class EnquiryRequest(BaseModel):
    query: str
    top_k: int = 6





    
from app.router.user import login_cache

@routeremb.get("/cache")
def get_cached_user(fusion_user_id: str):
    return login_cache.get(fusion_user_id, None)

@routeremb.get("/session")
async def get_session_data(request: Request):
    user_id = request.session.get("user_id")
    email = request.session.get("email")

    if not user_id:
        return {
            "status": "error",
            "message": "User not logged in"
        }

    return {
        "status": "success",
        "user_id": user_id,
        "email": email
    }



class EnquiryRequest(BaseModel):
    query: str
    top_k: int = 6


import json
import os
import uuid
import datetime
from fastapi import Request

ISSUE_HISTORY_FILE = "issue_history.json"

from fastapi import Request
import uuid
import datetime
import os
import json

@routeremb.post("/chat/{chat_id}")
async def chat_new(chat_id: str, request: Request):
    body = await request.json()

    query = body.get("query")
    top_k = 6

    if not query:
        return {"status": "error", "code": 400, "message": "Query is required"}

    session_id = "sess_" + uuid.uuid4().hex
    timestamp_user = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # Load or initialize history
    if os.path.exists(ISSUE_HISTORY_FILE):
        with open(ISSUE_HISTORY_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {}

    chat_id_str = str(chat_id)
    if chat_id_str not in data:
        data[chat_id_str] = {"sessions": []}

    # RAG search
    try:
        chunks_response = await search_all(query=query, top_k=top_k)
        matches = chunks_response.get("matches", [])
    except Exception:
        return {"status": "error", "code": 500, "message": "RAG search failed"}

    has_relevant_issue = False
    if matches:
        top_match = matches[0]
        if top_match.get("original_similarity", 0) >= 0.80:
            has_relevant_issue = True

    if has_relevant_issue:
        context_text = "\n\n".join(
            [f"[{c['source'].upper()}] {c['text']}" for c in matches]
        )
    else:
        from embeddings.embeddingsweb import dynamic_web_search_and_extract
        web_results = await asyncio.to_thread(dynamic_web_search_and_extract, query, 4)
        if web_results:
            context_text = "\n\n".join(
                [f"[WEB] Title: {r['title']} | URL: {r['url']} | Content: {r['text']}" for r in web_results]
            )
        else:
            context_text = "No relevant context found in Issue Repository or Web search."

    prompt = (
        "Role:\n"
        "You are an Oracle Fusion Knowledge Assistant for Consultants.\n"
        "Your responsibility is to provide precise, actionable, and implementation-focused guidance "
        "to Oracle Fusion consultants and support teams across Finance, SCM, and HCM.\n"
        "You are NOT a code generator. Do NOT provide SQL, PL/SQL, APIs, or scripts.\n"
        "Your answers must be structured, professional, and suitable for real-world consulting and support.\n\n"

        "IMPORTANT OUTPUT RULES:\n"
        "- Your response MUST be in valid Markdown format.\n"
        "- Do NOT use HTML.\n"
        "- Do NOT wrap the response in code blocks.\n"
        "- Use clear headings, bullets, and numbered steps only when appropriate.\n\n"

        "PRIMARY KNOWLEDGE SOURCE (STRICT PRIORITY):\n"
        "1. ALWAYS search the Issues Repository first.\n"
        "2. If a relevant issue exists, answer ONLY using the information from the Issues Repository.\n"
        "3. Do NOT use or refer to any File or Document repository.\n"
        "4. Use Web information ONLY when no relevant Issue exists.\n"
        "5. Never mix Issue Repository content with Web content.\n\n"

        "HISTORY-AWARE CONTINUOUS CHAT RULE (CRITICAL):\n"
        "- Always analyze the conversation history.\n"
        "- Determine whether the current question is:\n"
        "  a) A follow-up question.\n"
        "  b) A history/meta question.\n"
        "  c) A completely new question.\n\n"

        "GREETING RULE:\n"
        "- Respond with a greeting ONLY if the current user message is a greeting and there is no previous conversation.\n"
        "- Valid greetings include hi, hello, hey, good morning, good afternoon, good evening.\n"
        "- Greeting response:\n"
        "\"Hi, you are in Aspire Oracle Fusion Knowledge Assistant. How can I help you?\"\n\n"

        "ISSUE REPOSITORY RESPONSE RULE:\n"
        "- If the Issues Repository contains a relevant solution or information:\n"
        "- Use the Issue Repository as the ONLY knowledge source.\n"
        "- Look at all parts of the retrieved issue (including Issue Title, Issue Description, and Resolution Details) to form the answer. If a matching issue is retrieved, you must answer the user's query using the information in that issue, even if it is not related to Oracle Fusion. Avoid generic refusals if the matching issue provides an answer.\n"
        "- Expand the explanation only when required.\n"
        "- Do NOT search the Web.\n"
        "- At the end include:\n"
        "  Issue Reference: Issue ID <ID> - <Issue Title>\n\n"
        
        "WEB FALLBACK RULE:\n"
        "- Only when NO relevant issue exists in the Issues Repository:\n"
        "- Search official Oracle documentation.\n"
        "- Prefer Oracle Help Center, Oracle Documentation, Oracle Support, Oracle Learn and Oracle Cloud documentation.\n"
        "- Generate a complete consultant-oriented answer.\n"
        "- At the end include:\n"
        "  Source: Web\n"
        "  Document: <Document Title>\n"
        "  Reference: <Full URL>\n\n"
         "GENERAL KNOWLEDGE FALLBACK RULE (ONLY WHEN REQUIRED):\n"
        "- Apply this rule ONLY when no relevant Issue Repository data is available.\n"
        "- If no relevant issue exists, answer using your built-in knowledge.\n"
        "- Answer ONLY when you are reasonably confident in the correctness of the information.\n"
        "- Do NOT fabricate or invent Issue IDs, Oracle documentation, document titles, URLs, or references.\n"
        "- If Web documents are explicitly provided in the retrieved context, use them as supporting evidence and cite them.\n"
        "- If no Web documents are available in the retrieved context, do NOT generate or assume any Web reference.\n"
        "- In that case, end the response with:\n"
        "  Source: General Knowledge\n"
        "- If no relevant information is found in either the Issue Repository or official public Oracle/web documentation, you must respond with EXACTLY: \"I am not able to find any relevant information. Need more information.\"\n"
        "- This rule applies ONLY after the Issue Repository has been checked and no relevant information is found.\n\n"
        "STRICT RESPONSE RULES:\n"
        "- Never say you searched multiple sources.\n"
        "- Never mention File Repository.\n"
        "- Never mention Document Repository.\n"
        "- If Issue Repository answered the question, do not use Web.\n"
        "- If Web is used, clearly state 'Source: Web'.\n"
        "- Never fabricate references.\n"
        "- Only use URLs actually returned by the web search.\n\n"

        "User Question:\n"
        f"{query}\n\n"

        "Retrieved Context (Issues ):\n"
        f"{context_text}\n\n"

        "Mandatory Response Format\n"
        "## Problem Summary\n"
        "- One sentence describing the issue in plain terms.\n\n"

        "## Navigation / Task Path\n"
        "- Provide exact Oracle Fusion menu paths and task names.\n\n"

        "## Data Model / Subject Areas\n"
        "- List relevant module, functional area, tables, views, or subject areas.\n\n"

        "## Step-by-Step Solution\n"
        "- Numbered steps with exact task names, parameters, validations, and post-checks.\n\n"

        "## Common Pitfalls\n"
        "- List 2–4 common misconfigurations or functional risks.\n\n"

        "## References\n"
        "- Clearly specify the source used to prepare the response.\n"
        "- Include ONLY references that are explicitly available.\n"
        "- DO NOT create, assume, infer, or fabricate any reference.\n"
        "- Acceptable reference sources include ONLY:\n"
        "  1) Issue Reference\n"
        "     Format:\n"
        "     Issue Reference: Issue ID <ISSUE_ID> - <ISSUE_TITLE>\n"
        "     Use this ONLY when the answer is based on the Issues Repository.\n\n"

        "  2) Web Reference\n"
        "     Format:\n"
        "     Web Reference: <Document Title>\n"
        "     URL: <Full URL>\n"
        "     Use this ONLY when the answer is generated from Oracle documentation or another trusted web source.\n\n"

        "- If an Issue Reference is available, DO NOT include a Web Reference.\n"
        "- Use a Web Reference ONLY when no relevant Issue Reference exists.\n"
        "- If no reference is available, state:\n"
        "  **No references available.**\n\n"
        "Style & Tone:\n"
        "- Consultant-oriented.\n"
        "- Oracle Fusion implementation-focused.\n"
        "- Professional.\n"
        "- Clear and actionable.\n"
    )
    # prompt = (
    #     "Role:\n"
    #     "You are an Oracle Fusion Knowledge Assistant for Consultants.\n"
    #     "Your responsibility is to provide precise, actionable, and implementation-focused guidance "
    #     "to Oracle Fusion consultants and support teams across Finance, SCM, and HCM.\n"
    #     "You are NOT a code generator. Do NOT provide SQL, PL/SQL, APIs, or scripts.\n"
    #     "Your answers must be structured, professional, and suitable for real-world consulting and support.\n\n"

    #     "IMPORTANT OUTPUT RULES:\n"
    #     "- Your response MUST be in valid Markdown format.\n"
    #     "- Do NOT use HTML.\n"
    #     "- Do NOT wrap the response in code blocks.\n"
    #     "- Use clear headings, bullets, and numbered steps only when appropriate.\n\n"

    #     # -------------------- ADDITION START --------------------
    #     "SOURCE DETECTION ENFORCEMENT (STRICT EXECUTION RULE):\n"
    #     "- Before generating the response, you MUST inspect the 'Retrieved Context' and determine:\n"
    #     "  • Whether the top-ranked result source is 'issue', 'file', or 'web'.\n"
    #     "  • Whether both 'issue' and 'file' sources are present in relevant matches.\n"
    #     "- Formatting behavior MUST strictly follow the NEW SOURCE-BASED RESPONSE CONTROL rules.\n"
    #     "- Do NOT mix structured and non-structured formats unless both issue and file sources are present.\n"
    #     "- Do NOT ignore this rule.\n\n"
    #     # -------------------- ADDITION END --------------------

    #     "HISTORY-AWARE CONTINUOUS CHAT RULE (CRITICAL):\n"
    #     "- You MUST always analyze the full conversation history before answering.\n"
    #     "- Determine whether the current question is:\n"
    #     "  a) A follow-up to a previous response,\n"
    #     "  b) A meta/history-related question, OR\n"
    #     "  c) A completely new question.\n\n"

    #     "GREETING RULE (STRICT OVERRIDE):\n"
    #     "- Respond with a greeting ONLY if:\n"
    #     "  a) The CURRENT user message is exactly a greeting, AND\n"
    #     "  b) There is NO prior assistant response with explanations or steps.\n"
    #     "- Valid greetings: 'hi', 'hello', 'hey', 'good morning', "
    #     "'good afternoon', 'good evening'.\n"
    #     "- If matched, respond ONLY with:\n"
    #     "\"Hi, you are in Aspire Oracle Fusion Knowledge Assistant. How can I help you?\"\n"
    #     "- Otherwise, IGNORE the greeting rule.\n\n"

    #     "NEW SOURCE-BASED RESPONSE CONTROL (CRITICAL PRIORITY):\n"
    #     "- If the top relevant context comes from Issues Repository ONLY:\n"
    #     "  • Return ONLY the RESOLUTION_DETAILS clearly and directly.\n"
    #     "  • Do NOT use the structured Mandatory Response Format.\n"
    #     "  • Keep the response concise and resolution-focused.\n"
    #     "\n"
    #     "- If the relevant context comes ONLY from Files (Object Storage / Docs):\n"
    #     "  • You MUST use the full Mandatory Response Format.\n"
    #     "\n"
    #     "\n"
    #     "- If no relevant data is found in Issues Repository:\n"
    #     "  • Use official Oracle web documentation as fallback.\n"
    #     "  • Use the structured Mandatory Response Format.\n\n"

    #     # -------------------- ADDITION START --------------------
    #    "STRICT RESPONSE FORMAT VALIDATION RULE:\n"
    #     "- If source type = 'issue' ONLY → The response MUST NOT contain structured sections such as:\n"
    #     "  '## Problem Summary', '## Navigation / Task Path', '## Data Model / Subject Areas',\n"
    #     "  '## Step-by-Step Solution', or '## Common Pitfalls'.\n"
    #     "- If source type = 'file' ONLY → The response MUST include ALL sections defined under the Mandatory Response Format.\n"
    #     "- If source type includes BOTH 'issue' AND 'file' →\n"
    #     "  • The Issue resolution MUST appear FIRST in plain format (non-structured).\n"
    #     "  • This MUST be followed by a horizontal separator (---).\n"
    #     "  • Then include the full structured response based on file guidance.\n"
    #     "- If source type ALSO includes 'web' (along with issue and/or file) →\n"
    #     "  • The Web reference MUST be included in the References section along with issue and file references.\n"
    #     "\n"
    #     "- The References section MUST follow this exact formatting pattern when applicable:\n"
    # "  • Issue Reference: Issue ID <ID> – <Issue Title>\n"
    # "\n"
    # "- Web Reference: <Document Title> – <Full URL> MUST be included ONLY IF:\n"
    # "  • No valid Issue reference exists, AND\n"
    # "\n"


    #     "\n"
    #     "- Include ALL applicable references if multiple sources are used.\n"
    #     "- This rule overrides ALL other formatting decisions and must be strictly enforced.\n\n"

    #     # -------------------- ADDITION END --------------------

    #     "SOURCE ROUTING LOGIC (STRICT ORDER):\n"
    #     "1) Issues Repository (internal historical tickets/resolutions)\n"
    #     "   - If a matching issue exists, use the RESOLUTION as the primary source.\n"
    #     "   - If the resolution is generic or incomplete, continue to step 2.\n"
    #     "   - Always include the reference link of the top similar issue in the response.\n"
    #     "2) Public Oracle Content\n"
    #     "   - Use only official Oracle documentation or known issue pages.\n"
    #     "   - Always cite the source title and include the direct reference link of the top similar web result.\n\n"

    #     "User Question:\n"
    #     f"{query}\n\n"

    #     "Retrieved Context (Issues / Docs / Files):\n"
    #     f"{context_text}\n\n"

    #     "Mandatory Response Format (APPLY Always if data from file or web otherwise ignore it):\n"
    #     "## Problem Summary\n"
    #     "- One sentence describing the issue in plain terms.\n\n"

    #     "## Navigation / Task Path\n"
    #     "- Provide exact Oracle Fusion menu paths and task names.\n\n"

    #     "## Data Model / Subject Areas\n"
    #     "- List relevant module, functional area, tables, views, or subject areas.\n\n"

    #     "## Step-by-Step Solution\n"
    #     "- Numbered steps with exact task names, parameters, validations, and post-checks.\n\n"

    #     "## Common Pitfalls\n"
    #     "- List 2–4 common misconfigurations or functional risks.\n\n"

    #     "## References\n"
    #     "- Clearly specify the primary source used to prepare the response.\n"
    #     "- Include ONLY references that are explicitly present in the retrieved metadata.\n"
    #     "- DO NOT create, assume, infer, or fabricate any Issue, File, or Web reference.\n"
    #     "- Acceptable reference sources include ONLY the following three categories:\n"
    #     "  1) Issue Reference\n"
    #     "  2) File Reference\n"
    #     "  3) Web Reference\n"
    #     "- A category must be included ONLY if it exists in the retrieved metadata.\n"
    #     "- If a File Reference is not explicitly provided by the retrieval system (e.g., metadata.source = 'file'), DO NOT generate a File Reference.\n"
    #     "- If an Issue Reference is not explicitly present, DO NOT generate one.\n"
    #     "- If a Web Reference is not explicitly present, DO NOT generate one.\n"
    #     "- Each category name (Issue Reference, File Reference, Web Reference) must appear ONLY ONCE in the References section.\n"
    #     "- If multiple items belong to the same category, list them on the same line separated by semicolons (;).\n"
    #     "- Do NOT repeat the category label multiple times.\n"
    #     "- For File Reference: extract and display ONLY the actual file name from metadata.id (e.g., uploads/how-do-i-use-ai-agent-studio.pdf → how-do-i-use-ai-agent-studio.pdf). Do NOT create descriptive file names.\n"
    #     "- If no suitable reference is available for a category, omit that category entirely.\n"
    #     "- If no references at all are available, explicitly state:\n"
    #     "  **No references available.**\n\n"
    #     "Style & Tone:\n"
    #     "- Consultant-oriented, implementation-focused, and professional.\n"

    # )

    try:
        # Call the chatbot proxy asynchronously to avoid blocking the event loop
        answer = (await asyncio.to_thread(ask_ai, prompt)).strip()
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return {"status": "error", "code": 500, "message": "AI error"}

    timestamp_ai = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    history_item = {
        "session_id": session_id, 
        "timestamp": timestamp_user,
        "name": query,
        "USER": f"({timestamp_user}): {query}",
        "ASSISTANT": f"({timestamp_ai}): {answer}"
    }

    session_data = {
        "session_id": session_id,
        "chat_history": [history_item]
    }

    data[chat_id_str]["sessions"].append(session_data)

    with open(ISSUE_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

    return {
        "status": "success",
        "data": [{
            "chat_id": chat_id,
            "session_id": session_id,
            "query": query,
            "answer": answer
        }]
    }


@routeremb.post("/chat/{chat_id}/{session_id}")
async def chat_existing(chat_id: str, session_id: str, request: Request):
    body = await request.json()
    query = body.get("query")
    top_k = 6

    if not query:
        return {"status": "error", "code": 400, "message": "Query is required"}

    # Load history
    if os.path.exists(ISSUE_HISTORY_FILE):
        with open(ISSUE_HISTORY_FILE, "r") as f:
            data = json.load(f)
    else:
        return {"status": "error", "code": 404, "message": "History not found"}

    chat_id_str = str(chat_id)

    if chat_id_str not in data or "sessions" not in data[chat_id_str]:
        return {"status": "error", "code": 404, "message": "Chat ID not found"}

    # Find the session
    session_data = next(
        (s for s in data[chat_id_str]["sessions"] if s["session_id"] == session_id),
        None
    )
    if not session_data:
        return {"status": "error", "code": 404, "message": "Session not found"}

    timestamp_user = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    last_chats = session_data["chat_history"][-3:]

    context_text = ""

    for chat in last_chats:
        if "USER" in chat and chat["USER"]:
            context_text += chat["USER"] + "\n"

        if "ASSISTANT" in chat and chat["ASSISTANT"]:
            context_text += chat["ASSISTANT"] + "\n"

    print(context_text, "===== CONTEXT TEXT =====")


    # RAG search
    try:
        chunks_response = await search_all(query=query, top_k=top_k)
        matches = chunks_response.get("matches", [])
    except Exception:
        return {"status": "error", "code": 500, "message": "RAG search failed"}

    has_relevant_issue = False
    if matches:
        top_match = matches[0]
        if top_match.get("original_similarity", 0) >= 0.80:
            has_relevant_issue = True

    if has_relevant_issue:
        rag_context = "\n\n".join([f"[{c['source'].upper()}] {c['text']}" for c in matches])
    else:
        from embeddings.embeddingsweb import dynamic_web_search_and_extract
        web_results = await asyncio.to_thread(dynamic_web_search_and_extract, query, 4)
        if web_results:
            rag_context = "\n\n".join(
                [f"[WEB] Title: {r['title']} | URL: {r['url']} | Content: {r['text']}" for r in web_results]
            )
        else:
            rag_context = "No relevant context found in Issue Repository or Web search."

    prompt = (
    "Role:\n"
    "You are an Oracle Fusion Knowledge Assistant for Consultants.\n"
    "Your responsibility is to provide precise, actionable, and implementation-focused guidance "
    "to Oracle Fusion consultants and L2/L3 support teams across Finance, SCM, and HCM.\n"
    "You are NOT a code generator. Do NOT provide SQL, PL/SQL, REST, SOAP, Groovy, or scripts.\n"
    "Your responses must be professional, accurate, and suitable for real-world Oracle Fusion "
    "consulting and production support.\n\n"

    "IMPORTANT OUTPUT RULES:\n"
    "- All responses MUST be in valid Markdown format.\n"
    "- Do NOT use HTML.\n"
    "- Do NOT wrap responses in code blocks.\n\n"

    "HISTORY / META QUESTION HANDLING (CRITICAL - APPLY FIRST):\n"
    "- Apply this rule ONLY if the user is explicitly asking *about* their history, past questions, or metadata of the conversation (e.g. 'what did I ask before?', 'what was my last query?', 'show my previous questions').\n"
    "- STRIKE RULE: Do NOT apply this history/meta rule if the user is asking a standard query about developers, features, settings, or files, even if that same query was asked previously in the chat history.\n"
    "- If this rule applies:\n"
    "  • Look at the chat_history JSON array provided in context\n"
    "  • Find the MOST RECENT entry where USER field exists (ignore current question)\n"
    "  • Extract ONLY the text AFTER '(timestamp): ' from the USER field\n"
    "  • Respond with ONLY that extracted question text (trimmed and clean)\n"
    "  • Do NOT add explanations, formatting, or Mandatory Response Format\n"
    "  • Example: From 'USER': '(2026-02-19 13:27:19):  ODA AGENT integration' → Respond: 'ODA AGENT integration'\n\n"

    "GREETING RULE (STRICT OVERRIDE):\n"
    "- Apply ONLY if BOTH conditions true:\n"
    "  a) Current message is exactly: 'hi', 'hello', 'hey', 'good morning', 'good afternoon', 'good evening'\n"
    "  b) No prior ASSISTANT responses with explanations exist in chat_history\n"
    "- Respond ONLY with: \"Hi, you are in Aspire Oracle Fusion Knowledge Assistant. How can I help you?\"\n"
    "- Otherwise, IGNORE the greeting rule completely.\n\n"

    "HISTORY-AWARE CONTINUOUS CHAT RULE (MANDATORY):\n"
    "- You MUST always analyze the entire conversation history before answering.\n"
    "- Determine whether the current user message is:\n"
    "  a) History/meta question (asking about the chat log or past questions) → Apply HISTORY / META QUESTION HANDLING\n"
    "  b) Greeting → Apply GREETING RULE\n"
    "  c) Step-related follow-up → Apply STEP-RELATED FOLLOW-UP HANDLING\n"
    "  d) General follow-up (asking for more details or follow-up on the previous answer's topic) → Apply GENERAL FOLLOW-UP HANDLING\n"
    "  e) Completely new question or repeated domain question (asking about a functional/technical topic, even if repeated) → Apply MANDATORY RESPONSE FORMAT\n\n"

    "STEP-RELATED FOLLOW-UP HANDLING (MANDATORY – HISTORY-DRIVEN):\n"
    "- If the user asks anything related to steps, such as:\n"
    "  'explain step 4', 'previous step', 'clarify step mentioned above', 'details of that step':\n"
    "  • Look ONLY at the **Step-by-Step Solution** section from the MOST RECENT assistant response in chat_history\n"
    "  • Identify the referenced step number or closest relevant step\n"
    "  • Provide a SHORT, FOCUSED explanation ONLY for that step (1-3 sentences)\n"
    "  • Do NOT include Problem Summary, Navigation, Data Model, or other steps\n"
    "  • Respond as small resolution without structured headers\n\n"

    "GENERAL FOLLOW-UP HANDLING:\n"
    "- For clarification questions not explicitly referencing a step:\n"
    "  • Use the most recent assistant response in chat_history as PRIMARY context\n"
    "  • Answer ONLY what is being asked\n"
    "  • Keep response concise (2-4 sentences) and contextual\n"
    "  • Do NOT repeat full solutions or structured format\n\n"

    "NEW QUESTION HANDLING:\n"
    "- If the question introduces a new module, process, or functional topic NOT in recent chat_history:\n"
    "  • Treat it as a new discussion\n"
    "  • Apply the full MANDATORY RESPONSE FORMAT below\n\n"

    "STRICT RESPONSE FORMAT VALIDATION RULE:\n"
    "- If source type = 'issue' ONLY → Response in plain format, NO structured sections\n"
    "- If source type includes 'web' → Include Web Reference in References section\n\n"

    "GENERAL KNOWLEDGE FALLBACK RULE:\n"
    "- Apply this rule ONLY when no relevant Issue Repository data or Web search data is available, or if the retrieved information is not sufficient to answer the query.\n"
    "- If no relevant information is found in either the Issue Repository or official public Oracle/web documentation, you must respond with EXACTLY: \"I am not able to find any relevant information. Need more information.\"\n"
    "- Do NOT say 'I'm sorry, but no relevant information was found...' or anything else in this case.\n\n"

    "SOURCE ROUTING LOGIC (STRICT ORDER):\n"
    "1) Issues Repository → Use all parts of the retrieved issue (including Title, Description, and Resolution Details) to answer the query, even if it is not related to Oracle Fusion. Avoid generic refusals if the matching issue provides an answer. Include Issue Reference at the end.\n"
    "3) Public Oracle Content → Use only official docs + include Web Reference\n\n"

    "User Question:\n"
    f"{query}\n\n"

    "Conversation History:\n"
    f"{context_text}\n\n"

    "Retrieved Knowledge Context (RAG Results):\n"
    f"{rag_context}\n\n"
      # -------------------- ADDITION START --------------------
    "STRICT RESPONSE FORMAT VALIDATION RULE FOR NEW QUESTION NOT RELATED TO PREVIOUS RELATED QUESTION:\n"
    "- If source type = 'issue' ONLY → The response MUST NOT contain structured sections such as:\n"
    "  '## Problem Summary', '## Navigation / Task Path', '## Data Model / Subject Areas',\n"
    "  '## Step-by-Step Solution', or '## Common Pitfalls'.\n"
    "- If source type = 'file' ONLY → The response MUST include ALL sections defined under the Mandatory Response Format.\n"
    "- If source type includes BOTH 'issue' AND 'file' →\n"
    "  • The Issue resolution MUST appear FIRST in plain format (non-structured).\n"
    "  • This MUST be followed by a horizontal separator (---).\n"
    "  • Then include the full structured response based on file guidance.\n"
    "- If source type ALSO includes 'web' (along with issue and/or file) →\n"
    "  • The Web reference MUST be included in the References section along with issue and file references.\n"
    "\n"
    "MANDATORY RESPONSE FORMAT (NEW QUESTIONS ONLY):\n"
    "## Problem Summary\n"
    "- One sentence describing the issue in plain terms.\n\n"

    "## Navigation / Task Path\n"
    "- Provide exact Oracle Fusion menu paths and task names.\n\n"

    "## Data Model / Subject Areas\n"
    "- List Fusion module, functional area, task names.\n"
    "- Include OTBI subject areas if applicable.\n"
    "- If not applicable: 'Not applicable.'\n\n"

    "## Step-by-Step Solution\n"
    "- Numbered steps with exact task names, parameters, validations.\n\n"

    "## Common Pitfalls\n"
    "- List 2–4 common functional/configuration issues.\n\n"

 "## References\n"
    "- Clearly specify the primary source used to prepare the response.\n"
    "- Include ONLY references that are explicitly present in the retrieved metadata.\n"
    "- DO NOT create, assume, infer, or fabricate any Issue, File, or Web reference.\n"
    "- Acceptable reference sources include ONLY the following three categories:\n"
    "  1) Issue Reference\n"
    "  2) Web Reference\n"
    "- A category must be included ONLY if it exists in the retrieved metadata.\n"
    "- If an Issue Reference is not explicitly present, DO NOT generate one.\n"
    "- If a Web Reference is not explicitly present, DO NOT generate one.\n"
    "- Each category name (Issue Reference, Web Reference) must appear ONLY ONCE in the References section.\n"
    "- If multiple items belong to the same category, list them on the same line separated by semicolons (;).\n"
    "- Do NOT repeat the category label multiple times.\n"
    "- For File Reference: extract and display ONLY the actual file name from metadata.id (e.g., uploads/how-do-i-use-ai-agent-studio.pdf → how-do-i-use-ai-agent-studio.pdf). Do NOT create descriptive file names.\n"
    "- If no suitable reference is available for a category, omit that category entirely.\n"
    "- If no references at all are available, explicitly state:\n"
    "  **No references available.**\n\n"

    "Style & Tone:\n"
    "- Consultant-oriented, implementation-focused, professional.\n"
)


    try:
        # Call the chatbot proxy asynchronously to avoid blocking the event loop
        answer = (await asyncio.to_thread(ask_ai, prompt)).strip()
    except Exception as e:
        logger.error(f"AI chat error: {e}")
        return {"status": "error", "code": 500, "message": "AI error"}

    timestamp_ai = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    history_item = {
        "session_id": session_id, 
        "timestamp": timestamp_user,
        "name": query,
        "USER": f"({timestamp_user}): {query}",
        "ASSISTANT": f"({timestamp_ai}): {answer}"
    }

    session_data["chat_history"].append(history_item)

    # Save updated history
    with open(ISSUE_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

    return {
        "status": "success",
        "data": [
            {
                "chat_id": chat_id,
                "session_id": session_id,
                "query": query,
                "answer": answer
            }
        ]
    }






# Endpoint to fetch chat history
@routeremb.get("/chat_history")
@routeremb.get("/chat_history/{user_id}")
@routeremb.get("/chat_history/{user_id}/{session_id}")
async def get_chat_history(user_id: str = None, session_id: str = None):
    # Load the history file
    if os.path.exists(ISSUE_HISTORY_FILE):
        with open(ISSUE_HISTORY_FILE, "r") as f:
            data = json.load(f)
    else:
        data = {}

    # Case 1: /chat_history → return all data
    if not user_id:
        return {
            "status": "success",
            "data": data
        }

    # Case 2: /chat_history/{user_id} → return all sessions for that user
    if user_id and not session_id:
        user_data = data.get(user_id)
        if not user_data:
            return {"status": "error", "message": "User ID not found"}
        return {
            "status": "success",
            "user_id": user_id,
            "all_sessions": user_data
        }

 # Case 3: /chat_history/{user_id}/{session_id} → return specific session
    if user_id and session_id:
        user_data = data.get(user_id)
        if not user_data or "sessions" not in user_data:
            return {"status": "error", "message": "User ID not found"}

        session_data = None
        for s in user_data["sessions"]:
            if s["session_id"] == session_id:
                session_data = s
                break

        if not session_data:
            return {"status": "error", "message": "Session ID not found for this user"}

        return {
            "status": "success",
            "user_id": user_id,
            "session_id": session_id,
            "chat_history": session_data.get("chat_history", [])
        }




@routeremb.delete("/chat/{chat_id}")
@routeremb.delete("/chat/{chat_id}/{session_id}")
async def delete_chat_history(chat_id: str, session_id: str = None):
    if not os.path.exists(ISSUE_HISTORY_FILE):
        return {
            "status": "error",
            "code": 404,
            "message": "History file not found"
        }

    with open(ISSUE_HISTORY_FILE, "r") as f:
        data = json.load(f)

    chat_id_str = str(chat_id)

    if chat_id_str not in data:
        return {
            "status": "error",
            "code": 404,
            "message": "Chat ID not found"
        }

    # CASE 1: Delete ALL sessions for chat_id
    if session_id is None:
        del data[chat_id_str]

        with open(ISSUE_HISTORY_FILE, "w") as f:
            json.dump(data, f, indent=4)

        return {
            "status": "success",
            "message": f"All chat sessions deleted for chat_id '{chat_id}'"
        }

    # CASE 2: Delete specific session
    sessions = data[chat_id_str].get("sessions", [])

    updated_sessions = [
        s for s in sessions if s["session_id"] != session_id
    ]

    if len(updated_sessions) == len(sessions):
        return {
            "status": "error",
            "code": 404,
            "message": "Session ID not found"
        }

    # Update or cleanup
    if updated_sessions:
        data[chat_id_str]["sessions"] = updated_sessions
    else:
        del data[chat_id_str]

    with open(ISSUE_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

    return {
        "status": "success",
        "message": f"Session '{session_id}' deleted for chat_id '{chat_id}'"
    }






class ScrapURLRequest(BaseModel):
    url: str

@routeremb.post("/scrap-url")
async def scrap_url(payload: ScrapURLRequest):
    try:
        return await embed_web_url(payload.url)
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))





from embeddings.embeddingsweb import scrape_page_and_links,extract_links


class ScrapWithLinksRequest(BaseModel):
    url: str
    max_links: int = 20
    same_domain_only: bool = True

@routeremb.post("/scrapwithhyperlinks")
async def scrap_url_with_links(payload: ScrapWithLinksRequest):
    try:
        return await scrape_page_and_links(
            payload.url,
            max_links=payload.max_links,
            same_domain_only=payload.same_domain_only
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@routeremb.get("/fetch-hyperlinks")
def fetch_hyperlinks(url: str, same_domain_only: bool = True):
    try:
        response = requests.get(url, timeout=20, verify=False)
        response.raise_for_status()

        links = extract_links(
            base_url=url,
            html=response.text,
            same_domain_only=same_domain_only
        )

        return {
            "url": url,
            "total_links": len(links),
            "links": links
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))




@routeremb.get("/fetch-hyperlinks-js")
def fetch_hyperlinks_js(
    url: str = Query(..., description="Web page URL"),
    same_domain_only: bool = Query(
        True, description="Return only same-domain links"
    )
):
    try:
        links = fetch_hyperlinks_with_js(url, same_domain_only)
        return {
            "url": url,
            "total_links": len(links),
            "links": links
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))