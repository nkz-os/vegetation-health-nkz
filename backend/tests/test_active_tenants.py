"""A suspended tenant keeps its subscriptions; nothing is scheduled for it."""
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql

from app.models import VegetationSubscription
from app.services.active_tenants import tenant_is_active

TASKS = Path(__file__).resolve().parents[1] / "app" / "tasks"


def test_filter():
    engine = create_engine("sqlite://")
    sql = str(tenant_is_active().compile(dialect=postgresql.dialect()))
    with engine.begin() as c:
        c.execute(text("CREATE TABLE module_tenant_plan(tenant_id TEXT, is_active BOOLEAN)"))
        c.execute(text("INSERT INTO module_tenant_plan VALUES ('acme', 1), ('gone', 0)"))
        c.execute(text("CREATE TABLE vegetation_subscriptions(tenant_id TEXT)"))
        c.execute(text("INSERT INTO vegetation_subscriptions VALUES ('acme'), ('gone'), ('no-row')"))
        rows = c.execute(text(f"SELECT tenant_id FROM vegetation_subscriptions WHERE {sql}")).all()
    assert [r[0] for r in rows] == ["acme"]
    assert VegetationSubscription.__tablename__ in sql


def test_every_task_that_walks_subscriptions_uses_it():
    for name in ("scheduler.py", "lst_tasks.py", "monitoring_sync.py"):
        src = (TASKS / name).read_text()
        walks = len(re.findall(r"VegetationSubscription\.is_active == True", src))
        assert walks and src.count("tenant_is_active()") == walks, name
