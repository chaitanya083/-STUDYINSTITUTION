import os, tempfile, uuid, sys
from pathlib import Path
import pytest
from jose import jwt
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
p = tempfile.mkdtemp()
os.environ["AUTH_DEV_MODE"] = "true"
os.environ["APP_SECRET_KEY"] = "test-secret"
os.environ["DATABASE_URL"] = f"sqlite:///{p}/test.db"
from fastapi.testclient import TestClient
from app.main import app
from app.config import get_settings
settings = get_settings()

def auth_header(sub, roles, session_state=None):
    claims = {"sub": sub, "preferred_username": sub, "email": f"{sub}@test.local", "realm_access": {"roles": roles}}
    if session_state:
        claims["session_state"] = session_state
    return {"Authorization": f"Bearer {jwt.encode(claims, settings.app_secret_key, algorithm='HS256')}"}

@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c

@pytest.fixture
def super_headers():
    return auth_header("super-" + str(uuid.uuid4()), ["super_admin"])
