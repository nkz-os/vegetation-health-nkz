"""Regression test for get_db_with_tenant NameError (text/logger undefined).

Bug: database.py used `text(...)` and `logger.warning(...)` without importing
either, so every request through get_db_with_tenant raised NameError -> HTTP 500
(GET /overview, /data-status, /usage/current). See incident 2026-06-24.
"""
from unittest.mock import MagicMock, patch

import app.database as database


def _drain(gen):
    """Run a FastAPI generator-dependency to completion."""
    db = next(gen)
    try:
        next(gen)
    except StopIteration:
        pass
    return db


def test_get_db_with_tenant_sets_context_no_nameerror():
    """Happy path: SET runs, session yielded, no NameError."""
    fake = MagicMock()
    with patch.object(database, "SessionLocal", return_value=fake):
        db = _drain(database.get_db_with_tenant(tenant_id="montiko"))
    assert db is fake
    fake.execute.assert_called_once()
    fake.commit.assert_called_once()
    fake.close.assert_called_once()


def test_get_db_with_tenant_set_failure_warns_and_rolls_back():
    """If SET fails, the except branch must run WITHOUT raising NameError
    (logger defined) and must rollback the aborted transaction."""
    fake = MagicMock()
    fake.execute.side_effect = RuntimeError("boom")
    with patch.object(database, "SessionLocal", return_value=fake):
        # Must not raise (previously: NameError: name 'logger' is not defined)
        db = _drain(database.get_db_with_tenant(tenant_id="montiko"))
    assert db is fake
    fake.rollback.assert_called_once()
    fake.close.assert_called_once()
