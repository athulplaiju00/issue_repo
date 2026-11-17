from services.tls import VERIFY
import requests
from requests.auth import HTTPBasicAuth
import urllib3
import json, os
from uuid import uuid4
import datetime

FUSION_BASE_URL = "https://fa-esfe-dev11-saasfademo1.ds-fa.oraclepdemos.com"

def fusion_login(username: str, password: str):
    """
    Takes username & password from API body
    and verifies login using BasicAuth.
    If SCIM user ID is missing, fallback = username_section1
    """

    if not username or not password:
        return {
            "status": "error",
            "message": "Username and password are required"
        }

    test_url = f"{FUSION_BASE_URL}/fscmRestApi/resources/latest"

    try:
        # Step 1 — Test login
        login_resp = requests.get(
            test_url,
            auth=HTTPBasicAuth(username, password),
            headers={"Accept": "application/json"},
            verify=VERIFY
        )

        if login_resp.status_code == 401:
            return {
                "status": "error",
                "message": "Invalid username or password"
            }

        if login_resp.status_code == 200:

            # Step 2 — Try to fetch SCIM user ID
            scim_url = (
                f"{FUSION_BASE_URL}/hcmRestApi/scim/Users?"
                f'filter=userName%20eq%20"{username}"'
            )

            scim_resp = requests.get(
                scim_url,
                auth=HTTPBasicAuth(username, password),
                headers={"Accept": "application/json"},
                verify=VERIFY
            )

            fusion_user_id = None

            if scim_resp.status_code == 200:
                try:
                    data = scim_resp.json()
                    resources = data.get("Resources", [])
                    if resources:
                        fusion_user_id = resources[0].get("id")
                except:
                    fusion_user_id = None

            # ----------------------------------------
            # Fallback if fusion_user_id is missing → 
            # use username_section1
            # ----------------------------------------
            if not fusion_user_id:
                fusion_user_id = f"{username}_section1"

            return {
                "status": "success",
                "message": "Fusion login successful",
                "username": username,
                "password": password,
                "fusion_user_id": fusion_user_id
            }

        return {
            "status": "error",
            "message": f"Fusion error {login_resp.status_code}"
        }

    except Exception as e:
        return {
            "status": "error",
            "message": f"Fusion connection error: {e}"
        }



FUSION_HISTORY_FILE = "fusion_history.json"

def load_agent_history():
    if not os.path.exists(FUSION_HISTORY_FILE):
        return {}
    with open(FUSION_HISTORY_FILE, "r") as f:
        return json.load(f)

def save_agent_history(data):
    with open(FUSION_HISTORY_FILE, "w") as f:
        json.dump(data, f, indent=4)

def create_new_session():
    return f"sess_{uuid4().hex}"

def append_message(user_id, session_id, user_message, agent_message):
    data = load_agent_history()
    user_id = str(user_id)
 
    if user_id not in data:
        data[user_id] = {"sessions": []}
 
    session = next(
        (s for s in data[user_id]["sessions"] if s["session_id"] == session_id),
        None
    )
 
    if not session:
        session = {
            "session_id": session_id,
            "chat_history": []
        }
        data[user_id]["sessions"].append(session)
 
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
 
    session["chat_history"].append({
        "session_id": session_id,
        "timestamp": timestamp,
        "name": user_message,
        "USER": f"({timestamp}): {user_message}",
        "ASSISTANT": f"({timestamp}): {agent_message}"
    })
 
    save_agent_history(data)

def load_history(user_id, session_id):
    data = load_agent_history()
    user_sessions = data.get(str(user_id), {}).get("sessions", [])
    for session in user_sessions:
        if session["session_id"] == session_id:
            return session.get("agent_history", [])
    return []
