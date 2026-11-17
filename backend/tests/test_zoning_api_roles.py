"""Only technicians and admins edit a zoning; everyone reads it (real Postgres, fake broker)."""
from urllib.parse import quote

import pytest

from tests.test_zoning_api_accept import A, URL, _rows, _seed, api, as_roles  # noqa: F401  (fixture)
from tests.zoning_fakes import run

EDITORS = ["TechnicalConsultant", "TenantAdmin", "PlatformAdmin"]


def _zone(run_id):
    return run(run_id)[0]["id"]


def test_farmer_cannot_accept_note_or_merge(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A)
    as_roles(["Farmer"])
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 403
    note_url = f"{URL}/zones/{quote(_zone(A), safe='')}/note"
    assert client.post(note_url, json={"cause": "waterlogging"}).status_code == 403
    merge = {"run_id": A, "ranks": [1, 3], "target_rank": 3}
    assert client.post(f"{URL}/merge", json=merge).status_code == 403
    assert broker.appended == [] and broker.created == []
    assert _rows(dbf)[A].accepted_at is None


def test_role_match_is_exact(api):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A)
    as_roles(["TechnicalConsultantX", "technicalconsultant"])
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 403


@pytest.mark.parametrize("role", EDITORS)
def test_each_editor_role_may_accept(api, role):  # noqa: F811
    client, broker, dbf = api
    broker.store.update({e["id"]: e for e in run(A)})
    _seed(dbf, current=A)
    as_roles([role])
    assert client.post(f"{URL}/accept", json={"run_id": A}).status_code == 200


def test_reads_open_to_farmer(api):  # noqa: F811
    client, broker, dbf = api
    _seed(dbf, current=A)
    as_roles(["Farmer"])
    assert client.get(f"{URL}/versions").status_code == 200
