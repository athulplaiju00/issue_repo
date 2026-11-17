from services.tls import VERIFY
import os
import requests
import urllib3
from dotenv import load_dotenv

# Suppress insecure request warnings from urllib3

# Ensure environment variables are loaded
load_dotenv(override=True)

API_KEY = os.getenv("CHATBOT_API_KEY")
URL = os.getenv("CHATBOT_API_URL", "https://km-bot.aspiresystems.com/ot/v1/chat/completions")

def ask_ai(question):
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {API_KEY}"
    }

    data = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": question
            }
        ]
    }

    response = requests.post(
        URL,
        headers=headers,
        json=data,
        timeout=60,
        verify=VERIFY
    )

    response.raise_for_status()

    result = response.json()

    return result["choices"][0]["message"]["content"]
