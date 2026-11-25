"""Suspended tenants keep their subscriptions but nothing runs for them."""
from sqlalchemy import column, select, table

from app.models import VegetationSubscription

_plan = table("module_tenant_plan", column("tenant_id"), column("status"))


def tenant_is_active():
    """Filter over VegetationSubscription: the platform does not report the tenant as suspended."""
    return VegetationSubscription.tenant_id.in_(
        select(_plan.c.tenant_id).where(_plan.c.status != "suspended")
    )
