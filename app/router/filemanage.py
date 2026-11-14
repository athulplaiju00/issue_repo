import asyncio
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(__file__)))  # allow service imports
from embeddings.embeddingsfiles import embed_all_documents
from fastapi import APIRouter, File, UploadFile, HTTPException,BackgroundTasks,Query
from services.filemanagement import upload_to_object_storage,list_object_storage_files
from fastapi import FastAPI, HTTPException
import oci
import os
router = APIRouter(tags=["Object Storage"])

@router.post("/upload_to_object")
async def upload_to_object(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    base_url = (
        "https://objectstorage.ap-hyderabad-1.oraclecloud.com/"
        "p/Abf6eFq4OQG9yDDJNwXMtBrM219CqSojk54nm0G562-XiizP6ZBO44lMvPfMMckW/"
        "n/axir6m3xzsof/b/Knowledge_repo/o"
    )

    # Upload file (you already fixed uploads/ prefix inside this)
    result = upload_to_object_storage(base_url, file)

    # Trigger existing batch vectorization safely
    if result["status"] == "success":
        background_tasks.add_task(embed_all_documents)

    return result




# @router.get("/list_files")
# async def list_files():
#     """
#     Get a list of all uploaded files from Oracle Object Storage with clickable download URLs.
#     """
#     base_url = (
#         "https://objectstorage.ap-hyderabad-1.oraclecloud.com/"
#         "p/e4-IJFSzFcH5XOYWMi17QYf_HDh20NhtDYz9z-7fTLVTdBM1RYmiy6qX--UnCilU/"
#         "n/axir6m3xzsof/b/Knowledge_repo/o"
#     )

#     files = await asyncio.to_thread(list_object_storage_files, base_url)
#     if not files:
#         return {"message": "No files found in Object Storage."}
#     return {"count": len(files), "files": files}



@router.get("/list_files")
async def list_files(query: str | None = Query(default=None)):
    """
    Get files from Oracle Object Storage.
    If query is provided, return files starting with that name.
    """

    base_url = (
        "https://objectstorage.ap-hyderabad-1.oraclecloud.com/"
        "p/e4-IJFSzFcH5XOYWMi17QYf_HDh20NhtDYz9z-7fTLVTdBM1RYmiy6qX--UnCilU/"
        "n/axir6m3xzsof/b/Knowledge_repo/o"
    )

    # Fetch all files
    files = await asyncio.to_thread(list_object_storage_files, base_url)

    if not files:
        return {"message": "No files found in Object Storage."}

    # If query is passed → filter by prefix
    if query:
        query_lower = query.lower()
        files = [
            f for f in files
            if query_lower in f["file_name"].lower() 
        ]

    return {"count": len(files), "files": files}



# ⚠️ Be conscious: Below setup is at the **server level**.






# CONFIG_PATH = "/home/ubuntu/knowledge_repo/object_storage_setup/aspireysopnkeys/config"
# CONFIG_PROFILE = "DEFAULT"


# os.environ["SSL_CERT_FILE"] = "/home/ubuntu/knowledge_repo/object_storage_setup/aspireysopnkeys/DigiCertGlobalRootCA.crt.pem"

# try:
#     config = oci.config.from_file(CONFIG_PATH, CONFIG_PROFILE)
#     object_storage_client = oci.object_storage.ObjectStorageClient(config)
#     namespace = object_storage_client.get_namespace().data
#     print(f" Connected to OCI Object Storage. Namespace: {namespace}")
# except Exception as e:
#     print(" Failed to initialize OCI Object Storage client:", e)
#     raise


# @router.delete("/delete_file/")
# async def delete_file(bucket_name: str, object_name: str):
#     """
#     Delete a file from OCI Object Storage
#     Example:
#     DELETE /delete_file/?bucket_name=Knowledge_repo&object_name=uploads/mloperations.xlsx
#     """
#     try:
#         response = object_storage_client.delete_object(
#             namespace_name=namespace,
#             bucket_name=bucket_name,
#             object_name=object_name
#         )
#         return {"status": "success", "message": f"Deleted '{object_name}' from bucket '{bucket_name}'."}
#     except oci.exceptions.ServiceError as e:
#         raise HTTPException(status_code=e.status, detail=e.message)
#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))
