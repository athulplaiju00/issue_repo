from services.tls import VERIFY
import requests
import certifi
import ssl
import time
import logging
import urllib3
from models.issue_queries import connect_to_adw
from embeddings.embeddingsfiles import run_embed_all_documents_background

logger = logging.getLogger(__name__)
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
def upload_to_object_storage(base_url: str, file) -> dict:
    """
    Uploads a file to Oracle Object Storage.
    Only handles upload. No vectorization here.
    """

    conn = connect_to_adw()
    original_name = file.filename
    object_name = f"uploads/{original_name}"
    upload_url = f"{base_url}/{object_name}"

    try:
        # Check if already vectorized
        if file_exists_in_db(conn, object_name):
            return {
                "status": "success",
                "file_name": original_name,
                "uploaded_to": upload_url,
                "message": f"File '{original_name}' is already vectorized. Upload skipped."
            }

        file_bytes = file.file.read()

        response = requests.put(
            upload_url,
            data=file_bytes,
            verify=VERIFY,
            timeout=60
        )

        if response.status_code in (200, 201):
            return {
                "status": "success",
                "file_name": original_name,
                "uploaded_to": upload_url,
                "message": "File uploaded successfully. Vectorization will run in background."
            }

        return {
            "status": "error",
            "file_name": original_name,
            "message": f"Upload failed with HTTP {response.status_code}"
        }

    except Exception as e:
        return {
            "status": "error",
            "file_name": original_name,
            "message": f"Upload failed: {e}"
        }

    finally:
        conn.close()





logger = logging.getLogger(__name__)

def list_object_storage_files(base_url: str, prefix: str = "uploads/") -> list:
    try:
        list_url = f"{base_url}/?prefix={prefix}"
        logger.info(f"Fetching files from: {list_url}")

        response = requests.get(list_url, verify=VERIFY, timeout=30)
        if response.status_code != 200:
            logger.error(f"Failed to list objects: HTTP {response.status_code}")
            logger.error(response.text)
            return []

        data = response.json()
        objects = data.get("objects", [])
        if not objects:
            return []

        files = []
        for obj in objects:
            name = obj.get("name")
            if not name:
                continue

            files.append({
                "file_name": name.split("/")[-1],
                "path": name,
                "download_url": f"{base_url}/{name}"
            })

        return files

    except Exception as e:
        logger.error(f"Error fetching object list: {e}")
        return []





