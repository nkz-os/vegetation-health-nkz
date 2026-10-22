"""Monthly satellite-computation quota — reserve/report per-tenant usage
against the `max_satellite_computations` tier limit (migration 096, core
repo `nkz/config/timescaledb/migrations/`).

Limit source: `tenants.max_satellite_computations` — NOT `tenant_limits`,
which was dropped in migration 076 ("All limit columns now live ONLY in the
tenants table"). `NULL` = unlimited (enterprise tier).

Usage counter: `tenant_satellite_usage(tenant_id, period_month, computations)`
— one row per tenant per calendar month, PRIMARY KEY(tenant_id, period_month).

Both tables live in the platform DB (`nekazari`), reached via the existing
`platform_credentials._get_platform_db_connection()` helper — reused as-is,
not reimplemented here.

Fail-open per platform convention (mirrors entity-manager
parcel_activation.check_parcel_limit / check_and_reserve semantics): quota
enforcement must never block a computation, nor a usage report, because of
an infrastructure failure.
"""

import logging
from datetime import date

from app.services.platform_credentials import _get_platform_db_connection

logger = logging.getLogger(__name__)


class SatelliteQuota:
    """Reads/reserves a tenant's monthly satellite-computation quota."""

    @staticmethod
    def _current_period() -> date:
        """First day of the current month — the `period_month` key."""
        today = date.today()
        return date(today.year, today.month, 1)

    def _get_limit(self, tenant_id: str) -> int | None:
        """Read the tenant's monthly cap from `tenants.max_satellite_computations`.

        `None` = unlimited. Also `None` if the tenant row is missing (fail
        open — never block on an unresolvable tenant).

        Raises on connection/query failure; callers are responsible for the
        fail-open catch (this method deliberately does not swallow errors,
        so callers can distinguish "checked, unlimited" from "couldn't check").
        """
        conn = _get_platform_db_connection()
        if conn is None:
            raise RuntimeError("platform DB connection unavailable")
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT max_satellite_computations FROM tenants WHERE tenant_id = %s",
                    (tenant_id,),
                )
                row = cur.fetchone()
            finally:
                cur.close()
            return None if row is None else row[0]
        finally:
            conn.close()

    def _read_used(self, tenant_id: str, period: date) -> int:
        """Current-month `computations` count (0 if no row yet)."""
        conn = _get_platform_db_connection()
        if conn is None:
            raise RuntimeError("platform DB connection unavailable")
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    "SELECT computations FROM tenant_satellite_usage"
                    " WHERE tenant_id = %s AND period_month = %s",
                    (tenant_id, period),
                )
                row = cur.fetchone()
            finally:
                cur.close()
            return row[0] if row else 0
        finally:
            conn.close()

    def _increment_unconditionally(self, tenant_id: str, period: date) -> None:
        """Track usage for an unlimited tenant. Never gates — for reporting only."""
        conn = _get_platform_db_connection()
        if conn is None:
            raise RuntimeError("platform DB connection unavailable")
        try:
            cur = conn.cursor()
            try:
                # fiware-compliance: metadata-write — tenant_satellite_usage is a quota
                # counter (admin/metadata), NOT entity/timeseries/observational data, so a
                # direct Postgres write is the correct pattern per platform policy (Orion-LD
                # is only mandatory for telemetry/observational entities).
                cur.execute(
                    """
                    INSERT INTO tenant_satellite_usage (tenant_id, period_month, computations)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (tenant_id, period_month) DO UPDATE
                        SET computations = tenant_satellite_usage.computations + 1
                    """,
                    (tenant_id, period),
                )
            finally:
                cur.close()
            conn.commit()
        finally:
            conn.close()

    def _reserve_under_limit(self, tenant_id: str, period: date, limit: int) -> bool:
        """Atomic compare-and-increment: bump the counter iff still under `limit`.

        Single statement (INSERT ... ON CONFLICT DO UPDATE ... WHERE ...
        RETURNING) so concurrent reservations can't both slip past the cap.
        The plain-INSERT path (first computation of the month) can't exceed
        the limit either: callers only reach this method once `limit > 0`
        has already been confirmed, so an initial `computations = 1` is
        always within a positive limit.
        """
        conn = _get_platform_db_connection()
        if conn is None:
            raise RuntimeError("platform DB connection unavailable")
        try:
            cur = conn.cursor()
            try:
                # fiware-compliance: metadata-write — tenant_satellite_usage is a quota
                # counter (admin/metadata), NOT entity/timeseries/observational data, so a
                # direct Postgres write is the correct pattern per platform policy (Orion-LD
                # is only mandatory for telemetry/observational entities).
                cur.execute(
                    """
                    INSERT INTO tenant_satellite_usage (tenant_id, period_month, computations)
                    VALUES (%s, %s, 1)
                    ON CONFLICT (tenant_id, period_month) DO UPDATE
                        SET computations = tenant_satellite_usage.computations + 1
                        WHERE tenant_satellite_usage.computations < %s
                    RETURNING computations
                    """,
                    (tenant_id, period, limit),
                )
                row = cur.fetchone()
            finally:
                cur.close()
            conn.commit()
            return row is not None
        finally:
            conn.close()

    def get_usage(self, tenant_id: str) -> dict:
        """Best-effort current-month usage report. Never raises.

        Shape: {used:int, limit:int|None, remaining:int|None, period:"YYYY-MM"}.
        On any DB failure, returns an unknown/fail-open shape instead of
        raising (used=0, limit=None, remaining=None) so the caller (e.g. the
        `/usage` endpoint) always gets a 200 with a well-formed body.
        """
        period = self._current_period()
        period_str = period.strftime("%Y-%m")
        try:
            limit = self._get_limit(tenant_id)
            used = self._read_used(tenant_id, period)
            remaining = None if limit is None else max(0, limit - used)
            return {
                "used": used,
                "limit": limit,
                "remaining": remaining,
                "period": period_str,
            }
        except Exception as e:
            logger.error("Satellite quota usage lookup failed for tenant %s (fail-open/unknown): %s", tenant_id, e)
            return {
                "used": 0,
                "limit": None,
                "remaining": None,
                "period": period_str,
            }

    def check_and_reserve(self, tenant_id: str) -> bool:
        """Atomically increment this month's counter iff under the tenant's
        limit; return whether the reservation succeeded.

        - limit is None (unlimited): always reserve, return True.
        - limit is 0: always deny, return False (no row written).
        - otherwise: atomic compare-and-increment; True iff a row was
          updated/inserted within the cap.

        Fail-open: any DB error is logged and this returns True — quota
        enforcement must never block a computation on infrastructure
        failure (platform convention, see entity-manager
        parcel_activation.check_parcel_limit).
        """
        try:
            limit = self._get_limit(tenant_id)
            period = self._current_period()

            if limit is None:
                self._increment_unconditionally(tenant_id, period)
                return True

            if limit <= 0:
                return False

            return self._reserve_under_limit(tenant_id, period, limit)
        except Exception as e:
            logger.error("Satellite quota reservation failed for tenant %s (fail-open): %s", tenant_id, e)
            return True

    def release(self, tenant_id: str) -> None:
        """Refund one reserved unit for the current month (atomic decrement,
        floored at 0). Used to undo a `check_and_reserve` when the compute it
        was reserved for did not actually consume a genuine Sentinel Hub
        Processing Unit (runtime SH failure that degraded to the free local
        pipeline, or a selector error).

        Fail-open like the rest of the class: any DB error is logged and
        swallowed — a failed refund must never surface as a request error.
        """
        period = self._current_period()
        try:
            conn = _get_platform_db_connection()
            if conn is None:
                raise RuntimeError("platform DB connection unavailable")
            try:
                cur = conn.cursor()
                try:
                    cur.execute(
                        "UPDATE tenant_satellite_usage"
                        " SET computations = GREATEST(computations - 1, 0)"
                        " WHERE tenant_id = %s AND period_month = %s",
                        (tenant_id, period),
                    )
                finally:
                    cur.close()
                conn.commit()
            finally:
                conn.close()
        except Exception as e:
            logger.error("Satellite quota release failed for tenant %s (fail-open): %s", tenant_id, e)
