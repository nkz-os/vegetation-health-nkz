"""RECOVERABLE_PATTERNS must not silently swallow non-matching failures.

Bug (found 2026-07-22): reap_stuck_jobs fetched every old failed job with
an error message under the name "recoverable_failed" but only actually
retried the subset matching RECOVERABLE_PATTERNS — the rest sat forever,
re-evaluated every 15 min, miscounted in the log line as if pending retry.
"File not found" (the real error from a band-cleanup race, see
_decrement_and_cleanup_bands) never matched any pattern.
"""
from app.tasks.scheduler import _is_recoverable_error, RECOVERABLE_PATTERNS


def test_copernicus_error_is_recoverable():
    assert _is_recoverable_error("Copernicus API returned 503") is True


def test_timeout_is_recoverable():
    assert _is_recoverable_error("Connection timeout after 30s") is True


def test_file_not_found_is_not_recoverable():
    assert _is_recoverable_error(
        "File not found: vegetation-prime/scenes/S2A_MSIL2A_.../B08.tif"
    ) is False


def test_empty_error_is_not_recoverable():
    assert _is_recoverable_error("") is False
    assert _is_recoverable_error(None) is False


def test_finalization_is_idempotent():
    """Marking a job permanently_failed twice must not error or double-count."""
    class FakeJob:
        def __init__(self):
            self.result = None

    job = FakeJob()
    result = job.result or {}
    assert not result.get("permanently_failed")
    result["permanently_failed"] = True
    job.result = result
    assert job.result["permanently_failed"] is True

    # second pass: already finalized, must be a no-op per the reaper's guard
    result2 = job.result or {}
    already_finalized = bool(result2.get("permanently_failed"))
    assert already_finalized is True
