"""Daily monitoring sync: pause after the harvest, resume with the next sowing, widen to the period."""
import os
import sys
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")
os.environ.setdefault("CDSE_S3_ACCESS_KEY", "k")
os.environ.setdefault("CDSE_S3_SECRET_KEY", "s")

for _m in (
    "rasterio", "rasterio.warp", "rasterio.features", "rasterio.windows",
    "rasterio.transform", "rasterio.mask", "simpleeval",
):
    sys.modules.setdefault(_m, MagicMock())

import pytest
from celery.schedules import crontab

import app.tasks.monitoring_sync as ms
from app.celery_app import celery_app

TODAY = date(2026, 11, 19)
SOWN = date(2026, 3, 10)
HARVESTED = date(2026, 7, 5)
CURSOR = datetime(2026, 11, 1, 2, 0)


def boundary(day=None, provenance=None):
    return {"date": day.isoformat() if day else None, "provenance": provenance}


def cycle(species="Triticum aestivum", lifecycle="annual", start=SOWN, end=None, start_provenance="actual"):
    return {"species": species, "lifecycle": lifecycle, "start": boundary(start, start_provenance),
            "end": boundary(end, "actual" if end else None)}


def harvested(**kw):
    return {"current": None, "previous": cycle(end=HARVESTED, **kw)}


def growing(**kw):
    return {"current": cycle(**kw), "previous": None}


def make_sub(n=1, active=True, reason=None, start=date(2026, 3, 10)):
    return SimpleNamespace(id=n, tenant_id="tenant-a", entity_id=f"urn:ngsi-ld:AgriParcel:p{n}",
                           is_active=active, paused_reason=reason, start_date=start,
                           last_run_at=CURSOR, next_run_at=CURSOR)


class FakeDb:
    def __init__(self, subs):
        self.subs = subs
        self.commits = 0
        self.rollbacks = 0
        self.fail_commit_for = None

    def query(self, _model):
        return self

    def filter(self, *_a):
        return self

    def order_by(self, *_a):
        return self

    def all(self):
        return self.subs

    def commit(self):
        self.commits += 1
        if self.fail_commit_for is not None:
            raise RuntimeError("db down")

    def rollback(self):
        self.rollbacks += 1


@pytest.fixture
def broker(monkeypatch):
    """Timelines by parcel entity id; the continuity requests by entity id."""
    state = SimpleNamespace(timelines={}, requests={})

    async def fetch(tenant_id, parcel_urn):
        return state.timelines.get(parcel_urn)

    state.fetch = AsyncMock(side_effect=fetch)
    monkeypatch.setattr(ms, "fetch_timeline", state.fetch)
    monkeypatch.setattr(ms, "continuous_modules", lambda db, tenant, urn: state.requests.get(urn, []))
    return state


def run(subs, broker, today=TODAY):
    db = FakeDb(subs)
    return db, ms.sync_subscriptions(db, today)


def counts(**kw):
    base = {"paused": 0, "resumed": 0, "widened": 0, "unchanged": 0, "errors": 0}
    base.update(kw)
    return base


def test_a_person_s_pause_is_never_touched_and_its_crop_is_not_even_read(broker):
    sub = make_sub(1, active=False, reason="user")
    old = make_sub(2, active=False, reason=None)  # paused before the reason existed: a person's
    for s in (sub, old):
        broker.timelines[s.entity_id] = growing()
        broker.requests[s.entity_id] = ["carbon"]
    _, result = run([sub, old], broker)
    assert result == counts(unchanged=2)
    assert not sub.is_active and not old.is_active and sub.paused_reason == "user" and old.paused_reason is None
    broker.fetch.assert_not_awaited()


def test_a_failed_crop_read_changes_nothing_and_is_logged(broker, caplog):
    active, paused = make_sub(1), make_sub(2, active=False, reason="harvest")
    # no timeline known for either parcel: the fake answers None, as a broker failure does
    with caplog.at_level("WARNING"):
        db, result = run([active, paused], broker)
    assert result == counts(errors=2)
    assert active.is_active and active.start_date == SOWN and active.last_run_at == CURSOR
    assert not paused.is_active and paused.paused_reason == "harvest"
    assert db.commits == 0
    assert any("crop timeline" in m for m in caplog.messages)


def test_harvest_pauses_an_active_subscription(broker):
    sub = make_sub()
    broker.timelines[sub.entity_id] = harvested()
    db, result = run([sub], broker)
    assert result == counts(paused=1)
    assert sub.is_active is False and sub.paused_reason == "harvest"
    assert sub.start_date == SOWN and sub.last_run_at == CURSOR  # history and cursor stay
    assert db.commits == 1


def test_harvest_today_still_counts_as_monitored(broker):
    sub = make_sub()
    broker.timelines[sub.entity_id] = {"current": cycle(end=TODAY), "previous": None}
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and sub.is_active


def test_a_continuity_request_keeps_the_harvested_parcel_monitored_without_widening(broker):
    sub = make_sub()
    broker.timelines[sub.entity_id] = harvested()
    broker.requests[sub.entity_id] = ["carbon"]
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and sub.is_active and sub.paused_reason is None
    assert sub.start_date == SOWN
    _, again = run([sub], broker)
    assert again == counts(unchanged=1)


def test_an_olive_without_lifecycle_is_never_paused_by_its_harvest(broker):
    sub = make_sub(start=date(2025, 11, 19))
    broker.timelines[sub.entity_id] = {"current": None, "previous": cycle(
        species="Olea europaea", lifecycle="unknown", start=date(2015, 1, 1), end=HARVESTED)}
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and sub.is_active


def test_a_new_sowing_resumes_a_harvest_pause_from_the_sowing(broker):
    sub = make_sub(active=False, reason="harvest")
    resown = date(2026, 10, 20)
    broker.timelines[sub.entity_id] = {"current": cycle(start=resown), "previous": cycle(end=HARVESTED)}
    db, result = run([sub], broker)
    assert result == counts(resumed=1)
    assert sub.is_active is True and sub.paused_reason is None and sub.start_date == resown
    assert sub.last_run_at is None and sub.next_run_at is None and db.commits == 1


def test_a_harvest_pause_without_a_new_sowing_stays_paused(broker):
    sub = make_sub(active=False, reason="harvest")
    broker.timelines[sub.entity_id] = harvested()
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and not sub.is_active and sub.paused_reason == "harvest"


def test_a_planned_sowing_does_not_resume_a_harvest_pause(broker):
    sub = make_sub(active=False, reason="harvest")
    broker.timelines[sub.entity_id] = {"current": None, "previous": cycle(end=HARVESTED)}
    broker.timelines[sub.entity_id]["next"] = cycle(start=date(2026, 12, 1), start_provenance="planned")
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and not sub.is_active


def test_a_continuity_request_resumes_a_harvest_pause_from_the_sowing(broker):
    sub = make_sub(active=False, reason="harvest")
    broker.timelines[sub.entity_id] = harvested()
    broker.requests[sub.entity_id] = ["carbon"]
    _, result = run([sub], broker)
    assert result == counts(resumed=1)
    assert sub.is_active and sub.paused_reason is None and sub.start_date == SOWN
    assert sub.last_run_at is None and sub.next_run_at is None


def test_a_continuity_request_does_not_resume_a_persons_pause(broker):
    sub = make_sub(active=False, reason="user")
    broker.timelines[sub.entity_id] = harvested()
    broker.requests[sub.entity_id] = ["carbon"]
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and not sub.is_active and sub.paused_reason == "user"


def test_a_subscription_that_starts_after_the_period_is_widened_once(broker):
    sub = make_sub(start=TODAY - timedelta(days=60))
    broker.timelines[sub.entity_id] = growing()
    db, first = run([sub], broker)
    assert first == counts(widened=1)
    assert sub.start_date == SOWN and sub.last_run_at is None and sub.next_run_at is None
    sub.last_run_at = CURSOR  # the pass that followed moved the cursor
    sub.next_run_at = CURSOR
    db2, second = run([sub], broker)
    assert second == counts(unchanged=1) and sub.last_run_at == CURSOR and db2.commits == 0


def test_a_second_pass_changes_nothing_after_pause_and_resume(broker):
    paused = make_sub(1)
    broker.timelines[paused.entity_id] = harvested()
    run([paused], broker)
    _, again = run([paused], broker)
    assert again == counts(unchanged=1) and paused.paused_reason == "harvest"
    broker.timelines[paused.entity_id] = {"current": cycle(start=date(2026, 10, 20)), "previous": cycle(end=HARVESTED)}
    run([paused], broker)
    paused.last_run_at = CURSOR
    _, again = run([paused], broker)
    assert again == counts(unchanged=1) and paused.last_run_at == CURSOR


def test_a_resowing_without_a_harvest_keeps_the_earlier_start(broker):
    sub = make_sub(start=SOWN)
    broker.timelines[sub.entity_id] = growing(start=date(2026, 10, 20))
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and sub.start_date == SOWN and sub.last_run_at == CURSOR


def test_a_parcel_without_a_registered_sowing_keeps_its_default_window(broker):
    sub = make_sub(start=TODAY - timedelta(days=60))
    broker.timelines[sub.entity_id] = {"current": None, "previous": None}
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and sub.is_active and sub.start_date == TODAY - timedelta(days=60)


def test_a_failing_row_is_counted_and_the_others_still_run(broker):
    first, second = make_sub(1), make_sub(2)
    for s in (first, second):
        broker.timelines[s.entity_id] = harvested()
    db = FakeDb([first, second])
    def commit():
        db.commits += 1
        if db.commits == 1:
            raise RuntimeError("db down")

    db.commit = commit
    result = ms.sync_subscriptions(db, TODAY)
    assert result == counts(paused=1, errors=1)
    assert db.rollbacks == 1


def test_each_subscription_reads_its_crop_once(broker):
    subs = [make_sub(i) for i in range(1, 4)]
    for s in subs:
        broker.timelines[s.entity_id] = growing()
    run(subs, broker)
    assert broker.fetch.await_count == 3


def test_the_task_is_registered_and_scheduled_before_the_subscription_pass():
    entry = celery_app.conf.beat_schedule["vegetation.sync_monitoring"]
    assert entry["task"] == "vegetation.sync_monitoring"
    assert entry["schedule"] == crontab(hour=1, minute=30)
    assert "vegetation.sync_monitoring" in celery_app.tasks


# ── a person's pause survives the sync ───────────────────────────────────

def test_a_patch_pause_after_a_harvest_pause_is_never_resumed_by_the_sync(broker):
    import asyncio
    from app.api import subscriptions as subs_api

    sub = make_sub()
    broker.timelines[sub.entity_id] = harvested()
    run([sub], broker)
    assert sub.paused_reason == "harvest"

    class Db(FakeDb):
        def first(self):
            return sub

        def refresh(self, _obj):
            pass

    def patch_is_active(value):
        update = subs_api.SubscriptionUpdate(is_active=value)
        return asyncio.run(subs_api.update_subscription(
            "00000000-0000-0000-0000-000000000001", update, {"tenant_id": "tenant-a"}, Db([sub])))

    patch_is_active(True)
    assert sub.is_active is True and sub.paused_reason is None
    patch_is_active(False)
    assert sub.is_active is False and sub.paused_reason == "user"
    broker.timelines[sub.entity_id] = {"current": cycle(start=date(2026, 10, 20)), "previous": cycle(end=HARVESTED)}
    broker.requests[sub.entity_id] = ["carbon"]
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and not sub.is_active and sub.paused_reason == "user"


def test_a_patch_without_is_active_leaves_the_reason_alone(broker):
    import asyncio
    from app.api import subscriptions as subs_api

    sub = make_sub(active=False, reason="harvest")

    class Db(FakeDb):
        def first(self):
            return sub

        def refresh(self, _obj):
            pass

    asyncio.run(subs_api.update_subscription(
        "00000000-0000-0000-0000-000000000001", subs_api.SubscriptionUpdate(frequency="daily"),
        {"tenant_id": "tenant-a"}, Db([sub])))
    assert sub.paused_reason == "harvest" and sub.is_active is False


def test_a_deactivation_after_a_harvest_pause_is_a_person_s_and_the_sync_leaves_it(broker):
    from app.api import internal

    sub = make_sub()
    broker.timelines[sub.entity_id] = harvested()
    run([sub], broker)
    assert sub.paused_reason == "harvest"

    class Db(FakeDb):
        def first(self):
            return sub

    assert internal._deactivate_subscription(Db([sub]), "tenant-a", sub.entity_id) is True
    assert sub.paused_reason == "user"
    broker.timelines[sub.entity_id] = {"current": cycle(start=date(2026, 10, 20)), "previous": cycle(end=HARVESTED)}
    _, result = run([sub], broker)
    assert result == counts(unchanged=1) and not sub.is_active


def test_the_task_runs_on_the_utc_date(broker, monkeypatch):
    seen = []
    monkeypatch.setattr(ms, "utc_today", lambda: date(2031, 1, 2))
    monkeypatch.setattr(ms, "sync_subscriptions", lambda db, today: seen.append(today) or counts())
    monkeypatch.setattr(ms, "get_db_session", lambda: iter([MagicMock()]))
    ms.sync_monitoring()
    assert seen == [date(2031, 1, 2)]
