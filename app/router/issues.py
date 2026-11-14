from fastapi import APIRouter, Body, Path,BackgroundTasks
import asyncio
from fastapi import UploadFile, File, BackgroundTasks
import pandas as pd
import asyncio
import tempfile
import os
import pandas as pd
from embeddings.embeddings import run_store_issue_vectors_background
from models.issue_queries import (
connect_to_adw,
fetch_all_issues,
fuzzy_filter_description,
clean_records,insert_issue,
update_issue,
get_distinct_modules_and_status,
get_submodules_by_module,
fetch_files_by_issue_id,
delete_issue,
get_combined_summary_metrics)
import sys, os
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
router = APIRouter(tags=["Issues"])

@router.get("/issues")
async def get_issues(
    status: str | None = None,
    affected_module: str | None = None,
    issue_description: str | None = None,
    issue_id: int | None = None
):
    """
    Supports:
    /issues
    /issues?status="Draft"
    /issues?affected_module="Finance"
    /issues?issue_id=1001703
    /issues?status="Draft"&affected_module="Receivables"
    """

    # Fetch all issues from DB
    result = await asyncio.to_thread(fetch_all_issues)
    data = result.get("data", [])

    # Apply filters (only those that are provided)
    filtered_data = []
    for issue in data:
        issue_status = str(issue.get("status", "")).strip().lower()
        issue_module = str(issue.get("affected_module", "")).strip().lower()
        issue_id_val = str(issue.get("issue_id", "")).strip()

        # Apply filters in order
        if issue_id and issue_id_val != str(issue_id).strip():
            continue

        if status and issue_status != status.strip().lower().replace('"', ''):
            continue

        if affected_module and issue_module != affected_module.strip().lower().replace('"', ''):
            continue

        filtered_data.append(issue)

    # Apply fuzzy filter for description if provided
# Apply fuzzy filter for description if provided
    if issue_description:
        query = issue_description.strip('"')
        filtered_data = [
            issue for issue in filtered_data
            if query.lower() in str(issue.get("issue_title", "")).lower()
            or query.lower() in str(issue.get("issue_description", "")).lower()
        ]

    # Clean and prepare final data
    df = pd.DataFrame(filtered_data)
    if not df.empty:
        df.columns = df.columns.str.lower()
        df = df.where(pd.notnull(df), None)
        filtered_data = clean_records(df.to_dict(orient="records"))

    return {
        "filters": {
            "status": status,
            "affected_module": affected_module,
            "description": issue_description,
            "issue_id": issue_id
        },
        "distinct_issue_count": len(filtered_data),
        "data": filtered_data
    }




# ------------------- ROUTE -------------------
@router.get("/issue/{issue_id}")
async def get_issue(issue_id: int):
    """
    Fetch a single issue by its ISSUE_ID.
    Example:
        /issue/1002172
    """
    # Fetch all issues from DB (cached or real-time)
    result = await asyncio.to_thread(fetch_all_issues)
    data = result.get("data", [])

    # Find the issue with the matching ID
    single_issue = next((issue for issue in data if str(issue.get("issue_id")) == str(issue_id)), None)

    if single_issue:
        # Clean and normalize
        df = pd.DataFrame([single_issue])
        df.columns = df.columns.str.lower()
        df = df.where(pd.notnull(df), None)
        single_issue = df.to_dict(orient="records")[0]

        return {"data": [single_issue], "found": True}

    # Return empty if not found
    return {
        "data": [],
        "found": False,
        "message": f"Issue ID {issue_id} not found."
    }



@router.post("/issues")
async def add_issue(background_tasks: BackgroundTasks,payload: dict = Body(...)):
    """
    Insert a new issue directly into Oracle DB and return its generated ISSUE_ID.
    Handles None, 'null', and 'NaT' values gracefully.
    """
    try:
        # Normalize keys to uppercase for Oracle compatibility
        clean_payload = {}
        for k, v in payload.items():
            if (
                v in ("", None)
                or str(v).strip().lower() in ("null", "none", "nat")
            ):
                clean_payload[k.upper()] = None
            else:
                # Trim time part for date fields like "2025-08-17T14:11:08"
                if k.upper() in {"ISSUE_RAISED_DATE", "CREATED_DATE", "UPDATED_DATE"}:
                    if isinstance(v, str) and "T" in v:
                        v = v.split("T")[0]
                clean_payload[k.upper()] = v

        #  Call insert function in background thread
        result = await asyncio.to_thread(insert_issue, clean_payload)
        # Trigger embedding in background only for this new issue ID
        new_id = result.get("ISSUE_ID")
        if new_id:
            background_tasks.add_task(run_store_issue_vectors_background, [int(new_id)])     
        return {
            "message": "Issue inserted successfully",
            "issue_id": new_id,
            "created_by": result.get("CREATED_BY"),
        }
     
    except Exception as e:
        print("Error inserting issue:", e)
        return {
            "message": "Failed to insert issue",
            "error": str(e)
        }
    
@router.post("/issues/bulk-upload")
async def bulk_upload_issues(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    """
    Bulk upload issues from Excel into ISSUE_TRACKER.
    ISSUE_TITLE and ISSUE_DESCRIPTION are mandatory.
    After successful upload, VECTOR_ISSUES is rebuilt in the background.
    """

    try:
        # -----------------------------
        # Validate file type
        # -----------------------------
        if not file.filename.lower().endswith((".xlsx", ".xls")):
            return {
                "message": "Invalid file",
                "error": "Please upload an Excel (.xlsx/.xls) file."
            }

        # -----------------------------
        # Save uploaded file temporarily
        # -----------------------------
        with tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx") as tmp:
            tmp.write(await file.read())
            temp_file = tmp.name

        # -----------------------------
        # Read Excel
        # -----------------------------
        df = pd.read_excel(temp_file, dtype=str)
        os.remove(temp_file)

        # -----------------------------
        # Normalize column names
        # -----------------------------
        df.columns = [str(c).strip().upper() for c in df.columns]

        # -----------------------------
        # Expected Columns
        # -----------------------------
        expected_columns = [
            "ISSUE_ID",
            "ISSUE_TITLE",
            "ISSUE_DESCRIPTION",
            "AFFECTED_MODULE",
            "SUBMODULE",
            "SEVERITY",
            "IMPACT_AREA",
            "ROOT_CAUSE_IDENTIFIED",
            "ROOT_CAUSE_DETAILS",
            "RESOLUTION_DETAILS",
            "ORACLE_DOC_REFERENCE",
            "ISSUE_SOURCE",
            "ISSUE_RAISED_DATE",
            "ISSUE_REPORTED_BY",
            "STATUS",
            "REVIEWER_COMMENTS",
            "TAGS",
            "CREATED_DATE",
            "CREATED_BY",
            "UPDATED_DATE",
            "UPDATED_BY",
            "PROJECT_REFERENCE"
        ]

        # -----------------------------
        # Check Mandatory Columns
        # -----------------------------
        mandatory_columns = [
            "ISSUE_TITLE",
            "ISSUE_DESCRIPTION"
        ]

        missing_columns = [
            c for c in mandatory_columns if c not in df.columns
        ]

        if missing_columns:
            return {
                "message": "Invalid Excel",
                "error": f"Mandatory column(s) missing: {', '.join(missing_columns)}"
            }

        # -----------------------------
        # Validate Rows
        # -----------------------------
        validation_errors = []

        for idx, row in df.iterrows():

            if pd.isna(row.get("ISSUE_TITLE")) or str(row.get("ISSUE_TITLE")).strip() == "":
                validation_errors.append(
                    f"Row {idx + 2}: ISSUE_TITLE is mandatory"
                )

            if pd.isna(row.get("ISSUE_DESCRIPTION")) or str(row.get("ISSUE_DESCRIPTION")).strip() == "":
                validation_errors.append(
                    f"Row {idx + 2}: ISSUE_DESCRIPTION is mandatory"
                )

        if validation_errors:
            return {
                "message": "Validation failed",
                "rows_uploaded": 0,
                "errors": validation_errors
            }

        # -----------------------------
        # Pre-Validate Duplicate Titles (Excel & Database)
        # -----------------------------
        duplicate_titles = []
        seen_titles = set()

        # 1. Check duplicates within the Excel sheet itself
        for _, row in df.iterrows():
            title = row.get("ISSUE_TITLE") or row.get("issue_title")
            if title:
                title_str = str(title).strip().lower()
                if title_str in seen_titles:
                    duplicate_titles.append(str(title).strip())
                seen_titles.add(title_str)

        # 2. Check unique Excel titles against the database
        unique_excel_titles = list(seen_titles)
        if unique_excel_titles:
            conn = await asyncio.to_thread(connect_to_adw)
            try:
                with conn.cursor() as cur:
                    for title_str in unique_excel_titles:
                        cur.execute(
                            "SELECT COUNT(*) FROM ISSUE_TRACKER WHERE LOWER(TRIM(ISSUE_TITLE)) = LOWER(TRIM(:title))",
                            {"title": title_str}
                        )
                        if cur.fetchone()[0] > 0:
                            # Retrieve original case-sensitive title to display
                            orig_title = next(
                                (row.get("ISSUE_TITLE") or row.get("issue_title")
                                 for _, row in df.iterrows()
                                 if str(row.get("ISSUE_TITLE") or row.get("issue_title")).strip().lower() == title_str),
                                title_str
                            )
                            duplicate_titles.append(str(orig_title).strip())
            finally:
                conn.close()

        if duplicate_titles:
            # Deduplicate the list to display unique titles
            unique_dups = sorted(list(set(duplicate_titles)))
            if len(unique_dups) > 2:
                dup_list_str = f"'{unique_dups[0]}', '{unique_dups[1]}' etc."
            else:
                dup_list_str = ", ".join([f"'{d}'" for d in unique_dups])

            return {
                "message": f"Bulk upload failed. The following issue titles already exist: {dup_list_str} Please review your Excel file and try again.",
                "rows_uploaded": 0,
                "error": f"Error inserting issue: An issue with the title {dup_list_str} already exists."
            }

        # -----------------------------
        # Insert Records
        # -----------------------------
        inserted = 0
        inserted_ids = []

        for _, row in df.iterrows():

            clean_payload = {}

            for col in expected_columns:

                value = row[col] if col in df.columns else None

                if (
                    pd.isna(value)
                    or value in ("", None)
                    or str(value).strip().lower() in ("null", "none", "nat")
                ):
                    clean_payload[col] = None

                else:

                    if col in {
                        "ISSUE_RAISED_DATE",
                        "CREATED_DATE",
                        "UPDATED_DATE"
                    }:

                        if isinstance(value, str) and "T" in value:
                            value = value.split("T")[0]

                    clean_payload[col] = str(value).strip()

            # Ignore Excel ISSUE_ID
            clean_payload.pop("ISSUE_ID", None)

            result = await asyncio.to_thread(insert_issue, clean_payload)
            if result and result.get("ISSUE_ID"):
                inserted_ids.append(int(result.get("ISSUE_ID")))

            inserted += 1

        # -----------------------------
        # Rebuild Vector Table
        # -----------------------------
        if inserted_ids:
            background_tasks.add_task(
                run_store_issue_vectors_background,
                inserted_ids
            )

        return {
            "message": "Bulk upload completed successfully.",
            "rows_uploaded": inserted,
            "embedding_status": "Background embedding process started."
        }

    except Exception as e:

        import re

        error_msg = str(e)
        user_message = "Bulk upload failed. Please review your Excel file and try again."

        # ---------------------------------------------
        # ORA-01400 - Cannot insert NULL
        # ---------------------------------------------
        null_match = re.search(
            r'cannot insert NULL into \(".*?"\.".*?"\."(.*?)"\)',
            error_msg,
            re.IGNORECASE
        )

        if null_match:
            column = null_match.group(1)

            user_message = (
                f"Bulk upload failed. The mandatory column '{column}' contains empty or missing values. "
                f"Please populate '{column}' for all rows in the Excel file and upload again."
            )

        # ---------------------------------------------
        # ORA-00001 - Duplicate
        # ---------------------------------------------
        elif "ORA-00001" in error_msg:

            user_message = (
                "Bulk upload failed. Duplicate record detected. "
                "One or more rows already exist in the system. "
                "Please remove duplicate records and try again."
            )

        # ---------------------------------------------
        # ORA-12899 - Value too large
        # ---------------------------------------------
        elif "ORA-12899" in error_msg:

            length_match = re.search(
                r'column ".*?"\.".*?"\."(.*?)".*?actual: (\d+), maximum: (\d+)',
                error_msg,
                re.IGNORECASE
            )

            if length_match:

                column = length_match.group(1)
                actual = length_match.group(2)
                maximum = length_match.group(3)

                user_message = (
                    f"Bulk upload failed. The value in column '{column}' exceeds the allowed length "
                    f"({actual} characters provided, maximum allowed is {maximum}). "
                    "Please shorten the value and upload again."
                )

            else:

                user_message = (
                    "Bulk upload failed. One or more column values exceed the maximum allowed length."
                )

        # ---------------------------------------------
        # ORA-01861 - Invalid Date
        # ---------------------------------------------
        elif "ORA-01861" in error_msg:

            user_message = (
                "Bulk upload failed. One or more date fields contain invalid values. "
                "Please use the format YYYY-MM-DD."
            )

        # ---------------------------------------------
        # ORA-01843 - Invalid Month
        # ---------------------------------------------
        elif "ORA-01843" in error_msg:

            user_message = (
                "Bulk upload failed. One or more dates contain an invalid month. "
                "Please verify all date values."
            )

        # ---------------------------------------------
        # ORA-01722 - Invalid Number
        # ---------------------------------------------
        elif "ORA-01722" in error_msg:

            user_message = (
                "Bulk upload failed. One or more numeric fields contain invalid values. "
                "Please verify the numeric columns."
            )

        # ---------------------------------------------
        # ORA-00904 - Invalid Column
        # ---------------------------------------------
        elif "ORA-00904" in error_msg:

            invalid_col = re.search(
                r'ORA-00904:\s*"?(.*?)"?\s*:',
                error_msg,
                re.IGNORECASE
            )

            if invalid_col:

                user_message = (
                    f"Bulk upload failed. Invalid column '{invalid_col.group(1)}' found. "
                    "Please verify the Excel column names."
                )

            else:

                user_message = (
                    "Bulk upload failed due to an invalid database column."
                )

        # ---------------------------------------------
        # ORA-00942 - Table not found
        # ---------------------------------------------
        elif "ORA-00942" in error_msg:

            user_message = (
                "Bulk upload failed because the target database table could not be found. "
                "Please contact the administrator."
            )

        # ---------------------------------------------
        # ORA-01017 - Invalid DB credentials
        # ---------------------------------------------
        elif "ORA-01017" in error_msg:

            user_message = (
                "Bulk upload failed because the application could not connect to the database."
            )

        # ---------------------------------------------
        # ORA-03113 / ORA-03114
        # ---------------------------------------------
        elif "ORA-03113" in error_msg or "ORA-03114" in error_msg:

            user_message = (
                "Bulk upload failed because the database connection was interrupted. "
                "Please try again."
            )

        # ---------------------------------------------
        # File errors
        # ---------------------------------------------
        elif "Excel file format cannot be determined" in error_msg:

            user_message = (
                "The uploaded file is not a valid Excel file. "
                "Please upload a .xlsx or .xls file."
            )

        elif "No such file" in error_msg:

            user_message = (
                "The uploaded file could not be processed. Please upload the file again."
            )

        return {
            "message": user_message,
            "rows_uploaded": 0,
            "error": error_msg
        }

@router.put("/issues/{issue_id}")
async def edit_issue(
    issue_id: int = Path(..., description="Issue ID to update"),
    payload: dict = Body(...)
):
    """
    Update fields of an existing issue directly in DB.
    """
    updated_rows = await asyncio.to_thread(update_issue, issue_id, payload)
    if updated_rows == 0:
        return {"message": f"No issue found with issue_id {issue_id}"}
    return {"message": f"Issue {issue_id} updated successfully", "updated_fields": list(payload.keys())}

@router.get("/modules")
async def get_modules_and_status():
    """Get distinct modules and statuses."""
    data = await asyncio.to_thread(get_distinct_modules_and_status)
    return {"data": data}



@router.get("/submodules/{module}")
async def get_submodules(module: str):
    """Fetch distinct sub_module values for a given affected_module directly from DB."""
    submodules = await asyncio.to_thread(get_submodules_by_module, module)
    return {"submodules": submodules}


@router.delete("/issues/{issue_id}")
async def delete_issue_api(issue_id: int = Path(..., description="Issue ID to delete")):
    """
    Delete an issue by ISSUE_ID.
    """
    deleted_rows = await asyncio.to_thread(delete_issue, issue_id)
    if deleted_rows == 0:
        return {"message": f"No issue found with ISSUE_ID {issue_id}"}
    return {"message": f"Issue {issue_id} deleted successfully"}

from fastapi.responses import FileResponse, StreamingResponse, JSONResponse
import base64
import io

@router.get("/issues/{issue_id}/files")
async def get_issue_files(issue_id: int):
    files = await asyncio.to_thread(fetch_files_by_issue_id, issue_id)
    if not files:
        return {"message": f"No attachments found for ISSUE_ID {issue_id}"}
    return {"data": files}


from fastapi.responses import StreamingResponse
import io, base64

@router.get("/issues/{issue_id}/download")
async def download_file(issue_id: int):
    files = await asyncio.to_thread(fetch_files_by_issue_id, issue_id)
    if not files:
        return {"message": f"No attachment found for ISSUE_ID {issue_id}"}

    file_info = files[0]
    file_bytes = base64.b64decode(file_info["file_content_base64"])
    return StreamingResponse(
        io.BytesIO(file_bytes),
        media_type=file_info["mime_type"],
        headers={
            "Content-Disposition": f"attachment; filename={file_info['file_name']}"
        }
    )





@router.get("/issues/summary")
async def get_combined_summary():
    """Return combined metrics from USERS and ISSUE_TRACKER."""
    result = await asyncio.to_thread(get_combined_summary_metrics)
    return result