import os
import re
import oracledb
from dotenv import load_dotenv
import pandas as pd
from difflib import SequenceMatcher

load_dotenv(override=True)

# Oracle Autonomous Data Warehouse connection details
ADW_DB_USER = "ISSUE_REPOSITORY"
ADW_DB_PASSWORD = os.getenv("ADW_DB_PASSWORD")
ADW_DB_DSN = """(DESCRIPTION=(retry_count=20)(retry_delay=3)
(ADDRESS=(PROTOCOL=TCPS)(PORT=1522)(HOST=adb.ap-hyderabad-1.oraclecloud.com))
(CONNECT_DATA=(SERVICE_NAME=gd4a1f82d6494e9_ianalyticsadw_high.adb.oraclecloud.com))
(SECURITY=(SSL_SERVER_DN_MATCH=yes)))"""


# ------------------- DB CONNECTION -------------------
def connect_to_adw():
    """Create a new Oracle DB connection."""
    return oracledb.connect(user=ADW_DB_USER, password=ADW_DB_PASSWORD, dsn=ADW_DB_DSN)


# Oracle Autonomous Data Warehouse connection details
ADW_DB_USER_METADATA = "ORACLE_METADATA_KNOWLEDGE"
ADW_DB_PASSWORD_METADATA = os.getenv("ADW_DB_PASSWORD_METADATA")
ADW_DB_DSN_METADATA = """(DESCRIPTION=(retry_count=20)(retry_delay=3)
(ADDRESS=(PROTOCOL=TCPS)(PORT=1522)(HOST=adb.ap-hyderabad-1.oraclecloud.com))
(CONNECT_DATA=(SERVICE_NAME=gd4a1f82d6494e9_ianalyticsadw_high.adb.oraclecloud.com))
(SECURITY=(SSL_SERVER_DN_MATCH=yes)))"""


# ------------------- DB CONNECTION -------------------
def connect_to_metadata():
    """Create a new Oracle DB connection."""
    return oracledb.connect(user=ADW_DB_USER_METADATA, password=ADW_DB_PASSWORD_METADATA, dsn=ADW_DB_DSN_METADATA)



# ----------------------------
# HELPER: Convert Oracle rows to dict safely
# ----------------------------
def serialize_oracle_row(cur, row):
    cols = [desc[0].lower() for desc in cur.description]
    record = {}
    for col, val in zip(cols, row):
        if isinstance(val, oracledb.LOB):
            val = val.read()
        if val is None or str(val).strip().lower() in ("null", "none", "nat"):
            val = ""
        record[col] = val
    return record


# ------------------- Helper Functions -------------------
def clean_records(records: list[dict]) -> list[dict]:
    """Remove keys with None or NaN values from each record."""
    return [
        {k: v for k, v in r.items() if v is not None and not (isinstance(v, float) and pd.isna(v))}
        for r in records
    ]

def parse_oracle_style_q(q: str):
    """
    Parse Oracle-style query parameters like:
    status="Draft" AND affected_module="Finance" AND issue_id=1001703
    Supports both quoted and unquoted values.
    """
    filters = {}
    if not q:
        return filters

    # Match both quoted and unquoted
    matches = re.findall(r'(\w+)\s*=\s*(?:"([^"]+)"|([^\s]+))', q)
    for key, quoted, unquoted in matches:
        value = quoted or unquoted
        filters[key.lower()] = value.strip()
    return filters


def fuzzy_filter_description(rows, description):
    """Return only records where description similarity ≥ 70%."""
    filtered = []
    for issue in rows:
        desc_text = str(issue.get("issue_description", "")).lower()
        ratio = SequenceMatcher(None, desc_text, description.lower()).ratio()
        if ratio >= 0.7:
            issue["match_ratio"] = round(ratio * 100, 2)
            filtered.append(issue)
    return sorted(filtered, key=lambda x: x["match_ratio"], reverse=True)



# ------------------- DB CONNECTION -------------------
def connect_to_adw():
    """Create a new Oracle DB connection."""
    return oracledb.connect(user=ADW_DB_USER, password=ADW_DB_PASSWORD, dsn=ADW_DB_DSN)


# ------------------- FETCH ISSUES -------------------
def fetch_all_issues_filtered(status=None, affected_module=None, description=None):
    """
    Fetch issues from ISSUE_TRACKER with optional filters.
    - status: exact match (case-insensitive)
    - affected_module: exact match (case-insensitive)
    - description: fuzzy search (≥70% match)
    """
    try:
        conn = connect_to_adw()
        cur = conn.cursor()

        sql = """
            SELECT ISSUE_ID,
                   ISSUE_TITLE,
                   ISSUE_DESCRIPTION,
                   AFFECTED_MODULE,
                   SUBMODULE,
                   SEVERITY,
                   IMPACT_AREA,
                   ROOT_CAUSE_IDENTIFIED,
                   ROOT_CAUSE_DETAILS,
                   RESOLUTION_DETAILS,
                   ORACLE_DOC_REFERENCE,
                   ISSUE_SOURCE,
                   ISSUE_RAISED_DATE,
                   ISSUE_REPORTED_BY,
                   STATUS,
                   REVIEWER_COMMENTS,
                   TAGS,
                   CREATED_DATE,
                   CREATED_BY,
                   UPDATED_DATE,
                   UPDATED_BY,
                   PROJECT_REFERENCE
            FROM ISSUE_TRACKER
            WHERE 1=1
        """
        params = {}

        # Optional filters
        if status:
            sql += " AND LOWER(STATUS) = LOWER(:status)"
            params["status"] = status.strip()

        if affected_module:
            sql += " AND LOWER(AFFECTED_MODULE) = LOWER(:affected_module)"
            params["affected_module"] = affected_module.strip()

        sql += " ORDER BY ISSUE_ID DESC"
        cur.execute(sql, params)

        columns = [col[0] for col in cur.description]
        rows = []

        for row in cur:
            record = {}
            for col, val in zip(columns, row):
                if isinstance(val, oracledb.LOB):
                    val = val.read()
                if val is None or str(val).strip().lower() in ("nat", "none", "null"):
                    val = ""
                record[col.lower()] = val
            rows.append(record)

        cur.close()
        conn.close()

        # Fuzzy match filtering for description (≥70%)
        if description:
            filtered = []
            for issue in rows:
                desc_text = issue.get("issue_description", "").lower()
                ratio = SequenceMatcher(None, desc_text, description.lower()).ratio()
                if ratio >= 0.7:
                    issue["match_ratio"] = round(ratio, 2)
                    filtered.append(issue)
            rows = sorted(filtered, key=lambda x: x["match_ratio"], reverse=True)

        distinct_count = len({r.get("issue_id") for r in rows if r.get("issue_id")})

        return {"data": rows, "distinct_issue_count": distinct_count}

    except Exception as e:
        print(f"Error fetching issues: {e}")
        return {"data": [], "distinct_issue_count": 0}


# ------------------- FALLBACK (No Filter) -------------------
def fetch_all_issues():
    """Fetch all issues without filters."""
    return fetch_all_issues_filtered()




def insert_issue(issue: dict):
    """
    Insert a new issue into ISSUE_TRACKER and return ISSUE_ID and CREATED_BY.
    Automatically handles missing or null fields safely.
    """
    conn = connect_to_adw()
    try:
        cur = conn.cursor()

        # Check for duplicate issue by title (case-insensitive & trimmed)
        title = issue.get("ISSUE_TITLE") or issue.get("issue_title")
        if title:
            cur.execute(
                "SELECT COUNT(*) FROM ISSUE_TRACKER WHERE LOWER(TRIM(ISSUE_TITLE)) = LOWER(TRIM(:title))",
                {"title": str(title).strip()}
            )
            if cur.fetchone()[0] > 0:
                raise ValueError(f"An issue with the title '{title}' already exists.")

        # Generate next ISSUE_ID if not provided
        if "ISSUE_ID" not in issue or not issue["ISSUE_ID"]:
            cur.execute("SELECT NVL(MAX(ISSUE_ID), 0) + 1 FROM ISSUE_TRACKER")
            new_id = cur.fetchone()[0]
            issue["ISSUE_ID"] = new_id
        else:
            new_id = issue["ISSUE_ID"]

        # Expected columns (removed ATTACHMENT_FILE_NAME)
        columns = [
            "ISSUE_ID", "ISSUE_TITLE", "ISSUE_DESCRIPTION", "AFFECTED_MODULE", "SUBMODULE",
            "SEVERITY", "IMPACT_AREA", "ROOT_CAUSE_IDENTIFIED", "ROOT_CAUSE_DETAILS",
            "RESOLUTION_DETAILS", "ORACLE_DOC_REFERENCE", "ISSUE_SOURCE",
            "ISSUE_RAISED_DATE", "ISSUE_REPORTED_BY", "STATUS", "REVIEWER_COMMENTS", "TAGS",
            "CREATED_DATE", "CREATED_BY", "UPDATED_DATE", "UPDATED_BY", "PROJECT_REFERENCE"
        ]

        # Normalize keys and fill missing with None
        clean_issue = {}
        for col in columns:
            val = issue.get(col) or issue.get(col.lower())
            if val in ("", None) or str(val).strip().lower() in ("null", "none", "nat"):
                clean_issue[col] = None
            else:
                if col in {"ISSUE_RAISED_DATE", "CREATED_DATE", "UPDATED_DATE"} and isinstance(val, str) and "T" in val:
                    val = val.split("T")[0]
                clean_issue[col] = val

        # Build SQL with date handling (no attachment column)
        sql = """
        INSERT INTO ISSUE_TRACKER (
            ISSUE_ID, ISSUE_TITLE, ISSUE_DESCRIPTION, AFFECTED_MODULE, SUBMODULE,
            SEVERITY, IMPACT_AREA, ROOT_CAUSE_IDENTIFIED, ROOT_CAUSE_DETAILS,
            RESOLUTION_DETAILS, ORACLE_DOC_REFERENCE, ISSUE_SOURCE,
            ISSUE_RAISED_DATE, ISSUE_REPORTED_BY, STATUS, REVIEWER_COMMENTS, TAGS,
            CREATED_DATE, CREATED_BY, UPDATED_DATE, UPDATED_BY, PROJECT_REFERENCE
        ) VALUES (
            :ISSUE_ID, :ISSUE_TITLE, :ISSUE_DESCRIPTION, :AFFECTED_MODULE, :SUBMODULE,
            :SEVERITY, :IMPACT_AREA, :ROOT_CAUSE_IDENTIFIED, :ROOT_CAUSE_DETAILS,
            :RESOLUTION_DETAILS, :ORACLE_DOC_REFERENCE, :ISSUE_SOURCE,
            {issue_raised_date}, :ISSUE_REPORTED_BY, :STATUS, :REVIEWER_COMMENTS, :TAGS,
            {created_date}, :CREATED_BY, {updated_date}, :UPDATED_BY, :PROJECT_REFERENCE
        )
        """.format(
            issue_raised_date="TO_DATE(:ISSUE_RAISED_DATE, 'YYYY-MM-DD')" if clean_issue.get("ISSUE_RAISED_DATE") else "NULL",
            created_date="TO_DATE(:CREATED_DATE, 'YYYY-MM-DD')" if clean_issue.get("CREATED_DATE") else "NULL",
            updated_date="TO_DATE(:UPDATED_DATE, 'YYYY-MM-DD')" if clean_issue.get("UPDATED_DATE") else "NULL"
        )

        # Keep only parameters used in SQL
        valid_params = {k: v for k, v in clean_issue.items() if f":{k}" in sql}

        cur.execute(sql, valid_params)
        conn.commit()

        return {"ISSUE_ID": new_id, "CREATED_BY": clean_issue.get("CREATED_BY")}

    except Exception as e:
        raise Exception(f"Error inserting issue: {e}")
    finally:
        conn.close()







# ------------------- UPDATE -------------------
DATE_COLUMNS = {"ISSUE_RAISED_DATE", "CREATED_DATE", "UPDATED_DATE"}

def update_issue(issue_id: int, fields: dict):
    """Update an existing issue safely (handles None, NaT, and empty values)."""
    if not fields:
        raise ValueError("No fields to update")

    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        set_clauses = []
        params = {"ISSUE_ID": issue_id}

        for col, val in fields.items():
            col = col.upper()  # Normalize column names for Oracle

            # Handle NULL / NaT / empty / "null"
            if (
                val in ("", None)
                or str(val).strip().lower() in ("nat", "none", "null")
            ):
                set_clauses.append(f"{col} = NULL")
                continue

            # Handle DATE columns
            if col in DATE_COLUMNS:
                set_clauses.append(f"{col} = TO_DATE(:{col}, 'YYYY-MM-DD')")
                # Keep only date part if it contains time (e.g. 2025-08-17T14:11:08)
                if isinstance(val, str) and "T" in val:
                    val = val.split("T")[0]
                params[col] = val
            else:
                set_clauses.append(f"{col} = :{col}")
                params[col] = val

        # Prevent empty SQL if no valid columns
        if not set_clauses:
            raise ValueError("No valid fields to update")

        sql = f"UPDATE ISSUE_TRACKER SET {', '.join(set_clauses)} WHERE ISSUE_ID = :ISSUE_ID"

        print("DEBUG SQL:", sql)
        print("DEBUG PARAMS:", params)

        cur.execute(sql, params)
        conn.commit()
        return cur.rowcount

    except Exception as e:
        print(f"Error updating issue {issue_id}: {e}")
        raise
    finally:
        conn.close()


# ------------------- MODULE / SUBMODULE -------------------
def get_distinct_modules_and_status():
    """Return distinct AFFECTED_MODULE and STATUS values from ISSUE_TRACKER."""
    try:
        conn = connect_to_adw()
        cur = conn.cursor()

        # Fetch distinct AFFECTED_MODULE
        cur.execute("""
            SELECT DISTINCT AFFECTED_MODULE
            FROM ISSUE_TRACKER
            WHERE AFFECTED_MODULE IS NOT NULL
        """)
        modules = sorted({(row[0] or "").strip().title() for row in cur.fetchall() if row[0]})

        # Fetch distinct STATUS
        cur.execute("""
            SELECT DISTINCT STATUS
            FROM ISSUE_TRACKER
            WHERE STATUS IS NOT NULL
        """)
        statuses = sorted({(row[0] or "").strip().title() for row in cur.fetchall() if row[0]})

        cur.close()
        conn.close()

        return {
            "modules": modules,
            "statuses": statuses
        }

    except Exception as e:
        print("Error fetching distinct modules and statuses:", e)
        return {"modules": [], "statuses": []}



def get_submodules_by_module(module_name: str):
    """Return distinct SUBMODULE values for a given AFFECTED_MODULE."""
    try:
        conn = connect_to_adw()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT DISTINCT TRIM(SUBMODULE)
            FROM ISSUE_TRACKER
            WHERE LOWER(TRIM(AFFECTED_MODULE)) = LOWER(TRIM(:module))
              AND SUBMODULE IS NOT NULL
            """,
            {"module": module_name}
        )
        subs = [row[0] for row in cur.fetchall()]
        cur.close()
        conn.close()
        return sorted(subs)
    except Exception as e:
        print(f"Error fetching submodules for {module_name}:", e)
        return []




def get_distinct_issue_count():
    """Return the count of distinct ISSUE_ID values."""
    try:
        conn = connect_to_adw()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(DISTINCT ISSUE_ID) FROM ISSUE_TRACKER")
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception as e:
        print("Error fetching distinct issue count:", e)
        return 0




# ------------------- DELETE -------------------
def delete_issue(issue_id: int):
    """
    Delete an issue from ISSUE_TRACKER by ISSUE_ID.
    Returns number of rows deleted (0 if not found).
    """
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        sql = "DELETE FROM ISSUE_TRACKER WHERE ISSUE_ID = :ISSUE_ID"
        cur.execute(sql, {"ISSUE_ID": issue_id})
        conn.commit()
        return cur.rowcount
    except Exception as e:
        print(f"Error deleting issue: {e}")
        return 0
    finally:
        conn.close()




import base64
import oracledb

def fetch_files_by_issue_id(issue_id: int):
    """
    Fetch all files for a given ISSUE_ID safely with connection handling.
    """
    conn = None
    try:
        conn = connect_to_adw()   # always open fresh connection
        cur = conn.cursor()

        cur.execute("""
            SELECT FILE_NAME, MIME_TYPE, FILE_DATA, UPLOADED_DATE
            FROM file_uploads
            WHERE ISSUE_ID = :ISSUE_ID
        """, {"ISSUE_ID": issue_id})

        files = []
        for row in cur:
            file_name, mime_type, blob_data, uploaded_date = row

            if isinstance(blob_data, oracledb.LOB):
                blob_data = blob_data.read()
            encoded_content = base64.b64encode(blob_data).decode("utf-8") if blob_data else ""

            files.append({
                "issue_id": issue_id,
                "file_name": file_name or "",
                "mime_type": mime_type or "",
                "uploaded_date": str(uploaded_date) if uploaded_date else "",
                "file_content_base64": encoded_content
            })

        return files

    except Exception as e:
        print(f"Error fetching files for issue {issue_id}: {e}")
        return []

    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass




def get_combined_summary_metrics():
    """
    Fetch combined summary metrics from USERS and ISSUE_TRACKER tables:

    """
    conn = None
    cur = None

    try:
        conn = connect_to_adw()
        cur = conn.cursor()

        # 1️ Total distinct users
        cur.execute("SELECT COUNT(DISTINCT USER_ID) FROM USERS")
        total_users = cur.fetchone()[0] or 0

        # 2️ Active users (STATUS='ACTIVE')
        cur.execute("SELECT COUNT(DISTINCT USER_ID) FROM USERS WHERE UPPER(IS_ACTIVE) = 'ACTIVE'")
        active_users = cur.fetchone()[0] or 0

        # 3️ Distinct issues (unique ISSUE_IDs)
        cur.execute("SELECT COUNT(DISTINCT ISSUE_ID) FROM ISSUE_TRACKER")
        distinct_issues = cur.fetchone()[0] or 0

        # 4️ Total issues (all rows)
        cur.execute("SELECT COUNT(*) FROM ISSUE_TRACKER")
        total_issues = cur.fetchone()[0] or 0

        # 5️ Approved issues
        cur.execute("SELECT COUNT(*) FROM ISSUE_TRACKER WHERE UPPER(STATUS) = 'APPROVED'")
        approved_issues = cur.fetchone()[0] or 0

        # 6️ Active user percentage
        active_percentage = round((active_users / total_users) * 100, 2) if total_users else 0

        return {
            "total_users": total_users,
            "active_users": active_users,
            "active_user_percentage": f"{active_percentage}%",
            "distinct_issues": distinct_issues,
            "total_issues": total_issues,
            "approved_issues": approved_issues
        }

    except Exception as e:
        print(f"Error fetching combined summary metrics: {e}")
        return {
            "total_users": 0,
            "active_users": 0,
            "active_user_percentage": "0%",
            "distinct_issues": 0,
            "total_issues": 0,
            "approved_issues": 0,
            "error": str(e)
        }

    finally:
        try:
            if cur:
                cur.close()
            if conn:
                conn.close()
        except Exception:
            pass
