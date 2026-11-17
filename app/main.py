import sys, os, secrets
import certifi

# Corporate CA bundle for outbound HTTPS (see services/tls.py)
from services.tls import VERIFY  # noqa: F401

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
from starlette.middleware.sessions import SessionMiddleware
from app.router import issues, embedding, filemanage, user
sys.path.append(os.path.dirname(os.path.dirname(__file__)))

app = FastAPI(
    title="Knowledge Repo API",
    description="Issue tracking, embeddings, and file handling via FastAPI",
    version="3.0.0",
)

# Middlewares
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY") or secrets.token_hex(32)
)
allowed_origins = [
    "https://aimfusion.aspiresystems.com",
    "http://144.24.145.241:9004",
    "http://144.24.145.241",
    "http://localhost:9018",
    "http://localhost:9001",
    "http://127.0.0.1:9018",
    "http://127.0.0.1:9001",
    "http://localhost:3000",
    "http://localhost:4200",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include routers
app.include_router(issues.router)
app.include_router(embedding.routeremb)
app.include_router(filemanage.router)
app.include_router(user.router)

# if __name__ == "__main__":
#     uvicorn.run("app.main:app", host="0.0.0.0", port=5000, reload=False)
#latest feb 17


# =================== RUN SERVER ===================
if __name__ == "__main__":
    ssl_cert = "/etc/letsencrypt/archive/aspiresystems.in/cert1.pem"
    ssl_key = "/etc/letsencrypt/archive/aspiresystems.in/privkey1.pem"
    kwargs = {
        "host": "0.0.0.0",
        "port": 8000,
        "reload": False
    }
    if os.path.exists(ssl_cert) and os.path.exists(ssl_key):
        kwargs["ssl_certfile"] = ssl_cert
        kwargs["ssl_keyfile"] = ssl_key
    uvicorn.run("app.main:app", **kwargs)
