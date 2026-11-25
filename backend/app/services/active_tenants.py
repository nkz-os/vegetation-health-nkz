"""Suspended tenants keep their subscriptions but nothing runs for them."""
from sqlalchemy import column, select, table

from app.models import VegetationSubscription

_plan = table("module_tenant_plan", column("tenant_id"), column("is_active"))


def tenant_is_active():
    """Filter over VegetationSubscription: the platform says the tenant is active."""
    return VegetationSubscription.tenant_id.in_(
        select(_plan.c.tenant_id).where(_plan.c.is_active)
    )
