import datetime
from fastapi import APIRouter, Body 
import asyncio
from cachetools import TTLCache

from fastapi import Request
from services.user_service import (
    create_user,
    update_user,
    delete_user,
    login_user,
    view_all_users,
    fetch_user_by_id
)

router = APIRouter(prefix="/users", tags=["Users"])


# Create User
@router.post("/create")
async def create_user_api(payload: dict = Body(...)):
    """
    API endpoint to create a new user.
    Expects JSON with email, password, username, department, and role.
    """
    email = payload.get("email")
    password = payload.get("password")
    username = payload.get("username")
    department = payload.get("department")
    role = payload.get("role")

    # Call the function in a non-blocking thread
    result = await asyncio.to_thread(create_user, email, password, username, department, role)
    return result

#  Update User
@router.put("/update")
async def update_user_api(payload: dict = Body(...)):
    """
    API endpoint to update an existing user.
    Accepts fields: user_id (required), password, username, is_active, department, role
    """
    user_id = payload.get("user_id")
    password = payload.get("password")
    username = payload.get("username")
    is_active = payload.get("is_active")
    department = payload.get("department")
    role = payload.get("role")

    # Run the blocking DB call in a thread
    result = await asyncio.to_thread(update_user, user_id, password, username, is_active, department, role)
    return result


#  Delete User
@router.delete("/delete")
async def delete_user_api(payload: dict = Body(...)):
    user_id = payload.get("user_id")
    result = await asyncio.to_thread(delete_user, user_id)
    return result

login_cache = TTLCache(maxsize=100, ttl=3600)

# Login User
@router.post("/login")
async def login_user_api(request: Request, payload: dict = Body(...)):
    email = payload.get("email")
    password = payload.get("password")

    result = await asyncio.to_thread(login_user, email, password)

    if result.get("status") == "success":
        user_id = result.get("user_id")

        # store user info in session
        request.session["user_id"] = user_id
        request.session["email"] = email

        # optional: store in cache
        login_cache[user_id] = {
            "email": email,
            # avoid storing password unless absolutely required
            "login_time": str(datetime.datetime.now())
        }

    return result


# Get Session Data
@router.get("/session-data")
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


# @router.get("/session-data")
# async def get_session_data(request: Request):
#     return {
#         "status": "success",
#         "session": dict(request.session)
#     }

#  View All Users
@router.get("/all")
async def get_all_users():
    result = await asyncio.to_thread(view_all_users)
    return result



@router.get("/{user_id}")
async def get_user_by_id(user_id: int):
    """
    Fetch a single user's details by ID.
    Example: GET /users/5
    """
    try:
        result = await asyncio.to_thread(fetch_user_by_id, user_id)
        if result["status"] == "success":
            return {"status": "success", "code": 200, "data": result["data"]}
        else:
            return {"status": "error", "code": 404, "message": result["message"]}
    except Exception as e:
        return {"status": "error", "code": 500, "message": str(e)}