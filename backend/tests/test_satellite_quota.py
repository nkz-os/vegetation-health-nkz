"""Tests for the monthly satellite-computation quota service.

Mocks `_get_platform_db_connection` (imported into
app.services.satellite_quota) — no live DB required. Covers: reservation
under/at limit, limit 0 (always deny), limit None (unlimited, always
reserve), get_usage shape, and fail-open behavior on a DB error.
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from app.services.satellite_quota import SatelliteQuota


def _mock_conn(fetchone_results):
    """Build a MagicMock connection whose cursor().fetchone() yields
    `fetchone_results` in order across successive queries/cursors."""
    conn = MagicMock()
    cursor = MagicMock()
    cursor.fetchone.side_effect = fetchone_results
    conn.cursor.return_value = cursor
    return conn


@pytest.fixture
def quota():
    return SatelliteQuota()


class TestCurrentPeriod:
    def test_first_day_of_current_month(self, quota):
        today = date.today()
        assert quota._current_period() == date(today.year, today.month, 1)


class TestCheckAndReserve:
    def test_reserve_under_limit_increments_and_returns_true(self, quota):
        # limit query -> 10; reservation RETURNING -> new count 3 (was 2)
        conn = _mock_conn([(10,), (3,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            result = quota.check_and_reserve("tenant-a")

        assert result is True
        conn.commit.assert_called()
        # The reservation statement must be the atomic upsert with RETURNING.
        reserve_call = conn.cursor.return_value.execute.call_args_list[-1]
        assert "ON CONFLICT" in reserve_call.args[0]
        assert "RETURNING" in reserve_call.args[0]

    def test_reserve_at_limit_returns_false(self, quota):
        # limit query -> 5; reservation WHERE computations < 5 fails to
        # match (already at 5) -> RETURNING nothing -> fetchone() None
        conn = _mock_conn([(5,), None])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            result = quota.check_and_reserve("tenant-b")

        assert result is False

    def test_limit_zero_always_denies_without_inserting(self, quota):
        conn = _mock_conn([(0,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            result = quota.check_and_reserve("tenant-c")

        assert result is False
        # Only the limit SELECT ran; no INSERT/upsert against usage table.
        executed_sql = [
            call.args[0] for call in conn.cursor.return_value.execute.call_args_list
        ]
        assert len(executed_sql) == 1
        assert "tenants" in executed_sql[0]
        conn.commit.assert_not_called()

    def test_limit_none_always_reserves(self, quota):
        # limit query -> row with NULL value (enterprise/unlimited tenant)
        conn = _mock_conn([(None,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            result = quota.check_and_reserve("tenant-enterprise")

        assert result is True
        conn.commit.assert_called()

    def test_fail_open_on_db_error(self, quota):
        """DB unreachable -> log and return True, never block the computation."""
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            side_effect=RuntimeError("connection refused"),
        ):
            result = quota.check_and_reserve("tenant-d")

        assert result is True

    def test_fail_open_when_connection_is_none(self, quota):
        """_get_platform_db_connection() itself returns None on failure
        (its own internal catch) -- must also fail open, not raise."""
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=None,
        ):
            result = quota.check_and_reserve("tenant-e")

        assert result is True


class TestRelease:
    def test_release_decrements_current_month_floored_at_zero(self, quota):
        """release() issues a single atomic UPDATE that decrements the counter,
        floored at 0 via GREATEST(computations - 1, 0)."""
        conn = _mock_conn([])  # UPDATE returns no fetched row
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            result = quota.release("tenant-a")

        assert result is None
        conn.commit.assert_called_once()
        executed = conn.cursor.return_value.execute.call_args_list
        assert len(executed) == 1
        sql = executed[0].args[0]
        assert "UPDATE tenant_satellite_usage" in sql
        assert "GREATEST(computations - 1, 0)" in sql
        # Scoped to this tenant + the current period_month.
        params = executed[0].args[1]
        assert params[0] == "tenant-a"
        assert params[1] == quota._current_period()

    def test_release_fail_open_on_db_error(self, quota):
        """DB unreachable -> log and swallow, never raise (fail-open)."""
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            side_effect=RuntimeError("connection refused"),
        ):
            # Must not raise.
            assert quota.release("tenant-d") is None

    def test_release_fail_open_when_connection_is_none(self, quota):
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=None,
        ):
            assert quota.release("tenant-e") is None


class TestGetUsage:
    def test_usage_shape_under_limit(self, quota):
        # limit query -> 100; usage query -> 42
        conn = _mock_conn([(100,), (42,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            usage = quota.get_usage("tenant-a")

        expected_period = date.today().strftime("%Y-%m")
        assert usage == {
            "used": 42,
            "limit": 100,
            "remaining": 58,
            "period": expected_period,
        }

    def test_usage_shape_no_rows_yet(self, quota):
        # limit query -> 100; usage query -> no row for this tenant/month
        conn = _mock_conn([(100,), None])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            usage = quota.get_usage("tenant-fresh")

        assert usage["used"] == 0
        assert usage["limit"] == 100
        assert usage["remaining"] == 100

    def test_usage_shape_unlimited(self, quota):
        conn = _mock_conn([(None,), (7,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            usage = quota.get_usage("tenant-enterprise")

        assert usage["used"] == 7
        assert usage["limit"] is None
        assert usage["remaining"] is None

    def test_usage_remaining_never_negative(self, quota):
        # used exceeds limit somehow (e.g. limit lowered mid-month) -> clamp to 0
        conn = _mock_conn([(10,), (15,)])
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            return_value=conn,
        ):
            usage = quota.get_usage("tenant-over")

        assert usage["remaining"] == 0

    def test_get_usage_fail_open_shape_on_db_error(self, quota):
        with patch(
            "app.services.satellite_quota._get_platform_db_connection",
            side_effect=RuntimeError("connection refused"),
        ):
            usage = quota.get_usage("tenant-d")

        expected_period = date.today().strftime("%Y-%m")
        assert usage == {
            "used": 0,
            "limit": None,
            "remaining": None,
            "period": expected_period,
        }
