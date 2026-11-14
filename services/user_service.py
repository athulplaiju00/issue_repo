import oracledb
import bcrypt
from datetime import datetime
from models.issue_queries import connect_to_adw  # reuse your DB connection


# ✅ Create User (stores is_active as 'active' in lowercase)
def create_user(email: str, password: str, username: str = None, department: str = None, role: str = None):
    """
    Create a new user record in the USERS table.
    - Validates input fields
    - Hashes password securely with bcrypt
    - Checks for existing email before inserting
    - Automatically sets IS_ACTIVE = 'active' (lowercase)
    """

    # 🔹 Basic input validation
    if not email or not password:
        return {"status": "error", "message": "Email and password are required"}

    conn = connect_to_adw()
    try:
        cur = conn.cursor()

        # 🔹 Check if email already exists (case-insensitive)
        cur.execute("SELECT COUNT(*) FROM USERS WHERE LOWER(EMAIL) = LOWER(:email)", {"email": email})
        if cur.fetchone()[0] > 0:
            return {"status": "error", "message": f"Email '{email}' already exists"}

        # 🔹 Hash the password securely
        hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")

        # 🔹 Normalize and sanitize input
        email = email.strip()
        username = username.strip() if username else email.split("@")[0]
        department = department.strip() if department else None
        role = role.strip() if role else None

        # 🔹 Insert new user (store 'active' in lowercase)
        sql = """
        INSERT INTO USERS (EMAIL, PASSWORD_HASH, USERNAME, IS_ACTIVE, CREATED_AT, DEPARTMENT, ROLE)
        VALUES (:email, :password_hash, :username, 'active', SYSTIMESTAMP, :department, :role)
        RETURNING USER_ID INTO :user_id
        """

        user_id = cur.var(oracledb.NUMBER)
        cur.execute(sql, {
            "email": email,
            "password_hash": hashed_pw,
            "username": username,
            "department": department,
            "role": role,
            "user_id": user_id
        })

        conn.commit()

        # 🔹 Fetch new user_id
        uid_value = user_id.getvalue()
        uid = int(uid_value[0]) if isinstance(uid_value, list) else int(uid_value)

        return {
            "status": "success",
            "user_id": uid,
            "message": f"User '{username}' ({email}) created successfully and marked as active."
        }

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Database error: {e}"}

    finally:
        conn.close()


#  Update User (password, username, is_active, department, role)
def update_user(user_id: int, password=None, username=None, is_active=None, department=None, role=None):
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        fields = []
        params = {"user_id": user_id}

        if password:
            hashed_pw = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            fields.append("PASSWORD_HASH = :password_hash")
            params["password_hash"] = hashed_pw

        if username:
            fields.append("USERNAME = :username")
            params["username"] = username

        if is_active is not None:
            fields.append("IS_ACTIVE = :is_active")
            # Convert to string format
            if str(is_active).lower() in ["1", "true", "active", "y"]:
                params["is_active"] = "ACTIVE"
            else:
                params["is_active"] = "INACTIVE"

        if department:
            fields.append("DEPARTMENT = :department")
            params["department"] = department

        if role:
            fields.append("ROLE = :role")
            params["role"] = role

        if not fields:
            return {"status": "error", "message": "No fields to update"}

        sql = f"UPDATE USERS SET {', '.join(fields)} WHERE USER_ID = :user_id"
        cur.execute(sql, params)
        conn.commit()

        if cur.rowcount == 0:
            return {"status": "not_found", "message": f"No user found with USER_ID {user_id}"}

        return {"status": "success", "message": f"User {user_id} updated successfully"}

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


#  Delete User
def delete_user(user_id: int):
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM USERS WHERE USER_ID = :user_id", {"user_id": user_id})
        conn.commit()

        if cur.rowcount == 0:
            return {"status": "not_found", "message": f"No user found with USER_ID {user_id}"}

        return {"status": "success", "message": f"User {user_id} deleted successfully"}

    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


#  Login User
def login_user(email: str, password: str):
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT USER_ID, EMAIL, USERNAME, PASSWORD_HASH, IS_ACTIVE, DEPARTMENT, ROLE
            FROM USERS WHERE LOWER(EMAIL) = LOWER(:email)
        """, {"email": email})
        row = cur.fetchone()

        if not row:
            return {"status": "error", "message": "Invalid email or password"}

        user_id, user_email, username, stored_hash, is_active, department, role = row

        # Check active status (string now)
        if str(is_active).strip().upper() != "ACTIVE":
            return {"status": "error", "message": "User account is inactive"}

        if not bcrypt.checkpw(password.encode("utf-8"), stored_hash.encode("utf-8")):
            return {"status": "error", "message": "Invalid email or password"}

        return {
            "status": "success",
            "user_id": user_id,
            "username": username,
            "email": user_email,
            "department": department,
            "role": role
        }

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


#  View All Users
def view_all_users():
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT USER_ID, EMAIL, USERNAME, IS_ACTIVE, CREATED_AT, DEPARTMENT, ROLE
            FROM USERS ORDER BY CREATED_AT DESC
        """)
        columns = [c[0].lower() for c in cur.description]
        users = [dict(zip(columns, row)) for row in cur.fetchall()]

        return {"status": "success", "count": len(users), "data": users}

    except Exception as e:
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()


#  Fetch User by ID
def fetch_user_by_id(user_id: int):
    conn = connect_to_adw()
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT USER_ID, EMAIL, USERNAME, IS_ACTIVE, DEPARTMENT, ROLE, CREATED_AT
            FROM USERS WHERE USER_ID = :user_id
        """, {"user_id": user_id})
        row = cur.fetchone()

        if not row:
            return {
                "status": "error",
                "code": 404,
                "message": f"No user found with ID {user_id}"
            }

        columns = [c[0].lower() for c in cur.description]
        user = dict(zip(columns, row))

        return {
            "status": "success",
            "code": 200,
            "message": "User details fetched successfully",
            "data": user
        }

    except Exception as e:
        return {
            "status": "error",
            "code": 500,
            "message": f"Database error: {e}"
        }
    finally:
        conn.close()
