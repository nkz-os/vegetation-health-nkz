"""Shared test configuration.

App modules fail fast at import when required env vars are missing
(production mandate). Provide test values BEFORE any app import so the
suite can collect without a live environment.
"""

import os
import sys
from pathlib import Path

# Ensure this project's backend is FIRST on sys.path, overriding
# any editable installs (e.g. DaTaK/backend via _datak_gateway.pth)
# that would shadow our app package on import.
_backend_root = str(Path(__file__).resolve().parent.parent)
if _backend_root in sys.path:
    sys.path.remove(_backend_root)
sys.path.insert(0, _backend_root)

os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("ORION_URL", "http://orion:1026")
os.environ.setdefault("CONTEXT_URL", "http://context/ngsi-ld-context.json")
os.environ.setdefault("MINIO_ENDPOINT", "localhost:9000")
os.environ.setdefault("MINIO_ACCESS_KEY", "test")
os.environ.setdefault("MINIO_SECRET_KEY", "test")
os.environ.setdefault("INTERNAL_SERVICE_SECRET", "test-secret")
