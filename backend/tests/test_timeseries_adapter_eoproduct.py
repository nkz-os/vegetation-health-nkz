# backend/tests/test_timeseries_adapter_eoproduct.py
import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
from app.api import timeseries_adapter as ts


def test_history_reads_eoproduct_index_value():
    row = {
        "observed_at": datetime(2025, 9, 6, tzinfo=timezone.utc),
        "payload": {"ndvi": {"value": 0.72, "min": {"value": 0.3},
                              "max": {"value": 0.9}, "std": {"value": 0.1}}},
    }
    fake_cur = MagicMock(); fake_cur.fetchall.return_value = [row]
    fake_conn = MagicMock(); fake_conn.cursor.return_value = fake_cur
    with patch.object(ts.psycopg2, "connect", return_value=fake_conn) if hasattr(ts, "psycopg2") else patch("psycopg2.connect", return_value=fake_conn):
        pts = asyncio.run(ts._history_from_telemetry_events(
            "montiko", "urn:ngsi-ld:AgriParcel:da36ccd2-1111", "NDVI",
            datetime(2025, 1, 1, tzinfo=timezone.utc), datetime(2026, 1, 1, tzinfo=timezone.utc)))
    assert pts and pts[0]["mean"] == 0.72 and pts[0]["min"] == 0.3
    sql = fake_cur.execute.call_args.args[0]
    assert "EOProduct" in sql


def test_legacy_cache_reader_removed():
    assert not hasattr(ts, "_history_from_legacy_cache")


def test_arrow_endpoint_returns_multiple_points_for_parcel():
    """Arrow endpoint must key by parcel URN prefix, not exact EOProduct id (spec #2)."""
    rows = [
        {
            "observed_at": datetime(2025, 9, 6, tzinfo=timezone.utc),
            "payload": {"ndvi": {"value": 0.70}},
        },
        {
            "observed_at": datetime(2025, 9, 16, tzinfo=timezone.utc),
            "payload": {"ndvi": {"value": 0.75}},
        },
        {
            "observed_at": datetime(2025, 9, 26, tzinfo=timezone.utc),
            "payload": {"ndvi": {"value": 0.80}},
        },
    ]
    fake_cur = MagicMock()
    fake_cur.fetchall.return_value = rows
    fake_conn = MagicMock()
    fake_conn.cursor.return_value = fake_cur

    current_user = {"tenant_id": "montiko"}

    with patch("psycopg2.connect", return_value=fake_conn):
        resp = asyncio.run(
            ts.get_timeseries_data(
                entity_id="urn:ngsi-ld:AgriParcel:da36ccd2-1111",
                attribute="ndvi",
                start_time="2025-01-01T00:00:00Z",
                end_time="2026-01-01T00:00:00Z",
                resolution=None,
                format="arrow",
                current_user=current_user,
            )
        )

    sql = fake_cur.execute.call_args.args[0]
    params = fake_cur.execute.call_args.args[1]
    assert "LIKE" in sql and "EOProduct" in sql
    assert params[1] == "urn:ngsi-ld:EOProduct:montiko:da36ccd2-1:%"

    import pyarrow.ipc
    table = pyarrow.ipc.open_stream(resp.body).read_all()
    assert table.num_rows == 3
    assert table.column("value").to_pylist() == [0.70, 0.75, 0.80]
