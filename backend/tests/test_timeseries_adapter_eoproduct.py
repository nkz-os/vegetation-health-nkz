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
