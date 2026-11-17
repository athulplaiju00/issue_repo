"""
TLS trust for outbound HTTPS calls.

Certificate checking stays ON. The corporate proxy re-signs HTTPS traffic, so
CA_BUNDLE_PATH points to a PEM bundle that contains the proxy's root CA
(certifi + Windows trusted roots). Without it, requests use the default store.
"""
import os
from dotenv import load_dotenv

load_dotenv(override=True)

_bundle = (os.getenv("CA_BUNDLE_PATH") or "").strip()
if _bundle and os.path.exists(_bundle):
    # Also covers libraries that read these variables (ddgs, httpx, requests).
    os.environ["SSL_CERT_FILE"] = _bundle
    os.environ["REQUESTS_CA_BUNDLE"] = _bundle
    VERIFY = _bundle
else:
    VERIFY = True
