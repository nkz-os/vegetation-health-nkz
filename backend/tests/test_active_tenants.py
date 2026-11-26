"""A suspended tenant keeps its subscriptions; nothing is scheduled for it."""
import logging
import re
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.models import VegetationSubscription
from app.services.active_tenants import tenant_is_active

TASKS = Path(__file__).resolve().parents[1] / "app" / "tasks"


def _subscribed(db, plan_rows):
    if plan_rows is not None:
        db.execute(text("CREATE TABLE module_tenant_plan(tenant_id TEXT, status TEXT)"))
        for tenant, status in plan_rows:
            db.execute(text("INSERT INTO module_tenant_plan VALUES (:t, :s)"), {"t": tenant, "s": status})
    db.execute(text("CREATE TABLE vegetation_subscriptions(tenant_id TEXT)"))
    db.execute(text("INSERT INTO vegetation_subscriptions VALUES ('acme'), ('left'), ('gone'), ('no-row'), ('blank')"))
    sql = str(tenant_is_active(db).compile(dialect=postgresql.dialect(),
                                           compile_kwargs={"literal_binds": True}))
    assert VegetationSubscription.__tablename__ in sql or sql == "true"
    return [r[0] for r in db.execute(text(f"SELECT tenant_id FROM vegetation_subscriptions WHERE {sql}"))]


def test_only_suspended_tenants_are_skipped():
    with Session(create_engine("sqlite://")) as db:
        rows = _subscribed(db, [("acme", "active"), ("left", "cancelled"), ("gone", "suspended"),
                                ("blank", None), (None, "suspended")])
    assert rows == ["acme", "left", "no-row", "blank"]


def test_an_unreadable_view_skips_nobody_and_says_so(caplog):
    with Session(create_engine("sqlite://")) as db, caplog.at_level(logging.ERROR):
        rows = _subscribed(db, None)
    assert rows == ["acme", "left", "gone", "no-row", "blank"]
    assert "suspended tenants unavailable" in caplog.text


def test_every_task_that_walks_subscriptions_uses_it():
    for name in ("scheduler.py", "lst_tasks.py", "monitoring_sync.py"):
        src = (TASKS / name).read_text()
        walks = len(re.findall(r"VegetationSubscription\.is_active == True", src))
        assert walks and src.count("tenant_is_active(db)") == walks, name
