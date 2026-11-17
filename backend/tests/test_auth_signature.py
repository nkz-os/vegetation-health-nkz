"""The gateway's signature is verified, so no pod can pass as another tenant."""
import time

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

SECRET = "test-secret"


def _app():
    from app.middleware.auth import require_auth
    app = FastAPI()

    @app.get("/who")
    async def who(user: dict = Depends(require_auth)):
        return user

    return TestClient(app)


def _sign(token, tenant, ts=None):
    from nkz_platform_sdk.crypto import generate_hmac_signature
    return generate_hmac_signature(SECRET, token, tenant, ts)


@pytest.fixture
def enforcing(monkeypatch):
    from app.middleware import auth
    monkeypatch.setattr(auth, "REQUIRE_HMAC", True)
    monkeypatch.setattr(auth, "HMAC_SECRET", SECRET)


def _headers(tenant="acme", **extra):
    return {"X-Tenant-ID": tenant, "X-User-ID": "u-1", "X-User-Roles": "TechnicalConsultant", **extra}


def test_signed_by_gateway_passes(enforcing):
    h = _headers(Authorization="Bearer tok", **{"X-Auth-Signature": _sign("tok", "acme")})
    r = _app().get("/who", headers=h)
    assert r.status_code == 200, r.text
    assert r.json() == {"user_id": "u-1", "tenant_id": "acme", "roles": ["TechnicalConsultant"]}


def test_internal_call_signed_over_empty_token_passes(enforcing):
    r = _app().get("/who", headers=_headers(**{"X-Auth-Signature": _sign("", "acme")}))
    assert r.status_code == 200


def test_unsigned_is_401(enforcing):
    assert _app().get("/who", headers=_headers()).status_code == 401


def test_signature_for_another_tenant_is_401(enforcing):
    h = _headers(tenant="acme", **{"X-Auth-Signature": _sign("", "tenant-b")})
    assert _app().get("/who", headers=h).status_code == 401


def test_signature_over_another_token_is_401(enforcing):
    h = _headers(Authorization="Bearer tok", **{"X-Auth-Signature": _sign("other", "acme")})
    assert _app().get("/who", headers=h).status_code == 401


def test_expired_signature_is_401(enforcing):
    h = _headers(**{"X-Auth-Signature": _sign("", "acme", int(time.time()) - 600)})
    assert _app().get("/who", headers=h).status_code == 401


def test_without_enforcement_unsigned_still_passes(monkeypatch):
    from app.middleware import auth
    monkeypatch.setattr(auth, "REQUIRE_HMAC", False)
    assert _app().get("/who", headers=_headers()).status_code == 200


def test_enforcement_without_secret_refuses_to_start(monkeypatch):
    from app.middleware import auth
    monkeypatch.setattr(auth, "REQUIRE_HMAC", True)
    monkeypatch.setattr(auth, "HMAC_SECRET", "")
    with pytest.raises(RuntimeError):
        auth.check_signature_config()


def test_routes_reading_only_the_tenant_are_verified_too(enforcing):
    from app.middleware.auth import get_tenant_id
    app = FastAPI()

    @app.get("/tenant")
    def tenant(tenant_id: str = Depends(get_tenant_id)):
        return {"tenant": tenant_id}

    client = TestClient(app)
    assert client.get("/tenant", headers=_headers()).status_code == 401
    ok = client.get("/tenant", headers=_headers(**{"X-Auth-Signature": _sign("", "acme")}))
    assert ok.status_code == 200 and ok.json() == {"tenant": "acme"}
