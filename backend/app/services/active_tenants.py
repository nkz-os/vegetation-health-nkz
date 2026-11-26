"""Suspended tenants keep their subscriptions but nothing runs for them."""
import logging

from sqlalchemy import column, select, table, true
from sqlalchemy.exc import SQLAlchemyError

from app.models import VegetationSubscription

logger = logging.getLogger(__name__)

_plan = table("module_tenant_plan", column("tenant_id"), column("status"))


def tenant_is_active(db):
    """Filter over VegetationSubscription: skip only the tenants the platform reports as suspended.

    A tenant without a row or without a status still runs. If the platform view cannot be
    read, nobody is skipped: losing the run for every tenant is worse than one more run
    for a tenant on its way out.
    """
    try:
        with db.begin_nested():
            rows = db.execute(select(_plan.c.tenant_id).where(_plan.c.status == "suspended")).all()
    except SQLAlchemyError as exc:
        logger.error("suspended tenants unavailable, nothing skipped: %s", exc)
        return true()
    suspended = sorted({r[0] for r in rows if r[0] is not None})
    if not suspended:
        return true()
    return VegetationSubscription.tenant_id.notin_(suspended)
