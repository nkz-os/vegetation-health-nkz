"""eo_official_iri_expand: copy old-IRI EOProduct attributes to the official IRI, verify, then drop the old copy."""
import io

import pytest

from app.jobs import eo_official_iri_expand as job

OLD_SD = "https://nkz-os.org/ns/sensingDate"
OFF_SD = "https://smartdatamodels.org/dataModel.SatelliteImagery/sensingDate"
OFF_PT = "https://smartdatamodels.org/dataModel.SatelliteImagery/productType"
OFF_ZONE = "https://nkz-os.org/ns/hasAgriParcelZone"
DEFAULT_PT = "https://uri.etsi.org/ngsi-ld/default-context/productType"
DEFAULT_ZONE = "https://uri.etsi.org/ngsi-ld/default-context/hasAgriParcelZone"
SD = "2026-11-12T10:56:21Z"


def prop(v):
    return {"type": "Property", "value": v}


def rel(o):
    return {"type": "Relationship", "object": o}


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


class FakeSession:
    """Serves entities exactly as stored (read without Link: IRIs full, default-vocabulary terms short)."""

    def __init__(self, client):
        self.client = client
        self.gets = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.gets.append((url, dict(params or {}), dict(headers or {})))
        assert "Link" not in (headers or {}), "the read must not send a Link header"
        assert params["type"] == "EOProduct"
        off, lim = params["offset"], params["limit"]
        return FakeResponse(list(self.client.entities.values())[off:off + lim])


class FakeSyncClient:
    def __init__(self, tenant_id, entities):
        self.tenant_id = tenant_id
        self.entities = {e["id"]: e for e in entities}
        self._session = FakeSession(self)
        self.appended = []
        self.deleted = []
        self.fail_append = False

    def _url(self, path):
        return "http://orion" + path

    def _headers(self, content_type="application/ld+json"):
        h = {"NGSILD-Tenant": self.tenant_id, "Fiware-Service": self.tenant_id}
        if content_type == "application/json":
            h["Link"] = "<ctx>"
        return h

    def append_entity_attrs(self, entity_id, attrs, overwrite=True):
        self.appended.append((entity_id, dict(attrs), overwrite))
        if self.fail_append:
            raise RuntimeError("HTTP 400")
        ent = self.entities[entity_id]
        for k, v in attrs.items():
            assert k not in ent, "noOverwrite append of a present attribute"
            ent[k] = v

    def delete_entity_attr(self, entity_id, attr):
        self.deleted.append((entity_id, attr))
        # as stored: default-vocabulary terms are read short, but deleted by their full IRI
        key = attr[len("https://uri.etsi.org/ngsi-ld/default-context/"):] if attr.startswith(
            "https://uri.etsi.org/ngsi-ld/default-context/") else attr
        del self.entities[entity_id][key]


def old_scene(i, product_type="GRD"):
    return {"id": f"urn:ngsi-ld:EOProduct:tenant-a:{i}", "type": "EOProduct",
            OLD_SD: prop(SD), "productType": prop(product_type), "ndvi": prop(0.5)}


def run(entities, args=(), tenant="tenant-a"):
    client = FakeSyncClient(tenant, entities)
    out = io.StringIO()
    rc = job.main(["--tenant", tenant, *args], db=object(), client_factory=lambda t: client, out=out)
    return client, rc, out.getvalue()


@pytest.fixture(autouse=True)
def tenants(monkeypatch):
    monkeypatch.setattr(job, "_tenant_paths", lambda db, tenants: {t: "" for t in (tenants or ["tenant-a"])})


def test_dry_run_counts_without_writing():
    client, rc, out = run([old_scene("s1"), old_scene("s2")])
    assert rc == 0
    assert client.appended == []
    assert "would add" in out and "2" in out


def test_apply_adds_official_iri_keeps_old():
    client, rc, _ = run([old_scene("s1")], ["--apply"])
    assert rc == 0
    (eid, attrs, overwrite), = client.appended
    assert overwrite is False
    assert attrs[OFF_SD] == prop(SD)
    assert attrs[OFF_PT] == prop("GRD")
    assert OLD_SD not in attrs
    assert client.deleted == []
    assert OLD_SD in client.entities[eid]


def test_skips_entities_already_expanded():
    e = old_scene("s1")
    e[OFF_SD] = prop(SD)
    e[OFF_PT] = prop("GRD")
    client, rc, _ = run([e], ["--apply"])
    assert rc == 0
    assert client.appended == []


def test_grd_gets_official_product_type():
    e = old_scene("s1", "GRD")
    del e[OLD_SD]
    client, _, _ = run([e], ["--apply"])
    (_, attrs, _), = client.appended
    assert attrs == {OFF_PT: prop("GRD")}


def test_zone_relationship_copied_as_relationship():
    e = old_scene("z1")
    e["hasAgriParcelZone"] = rel("urn:ngsi-ld:AgriParcelZone:tenant-a:z")
    client, _, _ = run([e], ["--apply"])
    (_, attrs, _), = client.appended
    assert attrs[OFF_ZONE] == rel("urn:ngsi-ld:AgriParcelZone:tenant-a:z")


def test_append_400_counts_as_error():
    client = FakeSyncClient("tenant-a", [old_scene("s1"), old_scene("s2")])
    client.fail_append = True
    out = io.StringIO()
    rc = job.main(["--tenant", "tenant-a", "--apply"], db=object(), client_factory=lambda t: client, out=out)
    assert rc == 1
    assert len(client.appended) == 2  # one failing entity does not stop the others
    assert "errors=2" in out.getvalue()


def test_verify_reports_zero_missing_after_apply():
    client, _, _ = run([old_scene("s1"), old_scene("s2")], ["--apply"])
    out = io.StringIO()
    rc = job.main(["--tenant", "tenant-a", "--verify"], db=object(), client_factory=lambda t: client, out=out)
    assert rc == 0
    lines = out.getvalue().splitlines()
    sd = [l for l in lines if l.startswith("tenant-a\tsensingDate")][0]
    assert sd == "tenant-a\tsensingDate\told=2\tofficial=2\tmissing=0\tdiffering=0"


def test_verify_reports_differing_values():
    e = old_scene("s1")
    e[OFF_SD] = prop("1999-01-01")
    e[OFF_PT] = prop("GRD")
    _, rc, out = run([e], ["--verify"])
    assert rc == 1
    assert "tenant-a\tsensingDate\told=1\tofficial=1\tmissing=0\tdiffering=1" in out
    assert "DIFFERING\ttenant-a\turn:ngsi-ld:EOProduct:tenant-a:s1\tsensingDate" in out


def test_contract_deletes_old_copy_only_when_official_present():
    done = old_scene("s1")
    done[OFF_SD] = prop(SD)
    done[OFF_PT] = prop("GRD")
    done["hasAgriParcelZone"] = rel("urn:z")
    done[OFF_ZONE] = rel("urn:z")
    client, rc, _ = run([done], ["--contract", "--apply"])
    assert rc == 0
    assert sorted(a for _, a in client.deleted) == sorted([OLD_SD, DEFAULT_PT, DEFAULT_ZONE])
    # official copies untouched
    assert OFF_SD in client.entities[done["id"]] and OFF_PT in client.entities[done["id"]]


def test_contract_without_apply_only_counts():
    done = old_scene("s1")
    done[OFF_SD] = prop(SD)
    done[OFF_PT] = prop("GRD")
    client, rc, out = run([done], ["--contract"])
    assert rc == 0 and client.deleted == []
    assert "would delete" in out


def test_contract_refuses_when_verify_not_clean():
    missing = old_scene("s1")  # no official copy yet
    client, rc, out = run([missing], ["--contract", "--apply"])
    assert rc == 2 and client.deleted == []
    assert "REFUSED" in out

    differing = old_scene("s2")
    differing[OFF_SD] = prop("1999-01-01")
    differing[OFF_PT] = prop("GRD")
    client, rc, _ = run([differing], ["--contract", "--apply"])
    assert rc == 2 and client.deleted == []


def test_read_paginates_until_short_page():
    ents = [old_scene(f"s{i}") for i in range(1500)]
    client, _, _ = run(ents)
    offsets = [p["offset"] for _, p, _ in client._session.gets]
    assert offsets == [0, 1000]
    assert all(p["limit"] == 1000 for _, p, _ in client._session.gets)
