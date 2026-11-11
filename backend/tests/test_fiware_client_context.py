"""The legacy broker client always sends the platform @context: without it the entities it writes
(management zones) are stored under the default vocabulary and no other module can filter them."""
import pytest

from app.services import fiware_integration as fi

PLATFORM = "http://api-gateway-service:5000/ngsi-ld-context.json"


def test_sends_the_platform_context_by_default(monkeypatch):
    monkeypatch.setattr(fi, "CONTEXT_URL", PLATFORM)
    client = fi.FIWAREClient("http://broker.invalid", tenant_id="tenant-a")
    link = client.session.headers.get("Link", "")
    assert link.startswith(f"<{PLATFORM}>") and 'rel="http://www.w3.org/ns/json-ld#context"' in link


def test_refuses_to_run_without_a_context(monkeypatch):
    monkeypatch.setattr(fi, "CONTEXT_URL", "")
    with pytest.raises(RuntimeError):
        fi.FIWAREClient("http://broker.invalid", tenant_id="tenant-a")


def test_the_module_default_is_the_platform_context():
    assert fi.CONTEXT_URL  # never empty unless explicitly set so
