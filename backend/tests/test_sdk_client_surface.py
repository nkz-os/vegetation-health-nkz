"""No module code may call a method the Orion SDK does not expose.

Twelve call sites used `orion.get(...)` / `orion.delete(...)`. SyncOrionClient
has never had either — it exposes get_entity, delete_entity and query_entities.
Every one of those raised AttributeError and was swallowed by the surrounding
except, so SAR change detection and EOProduct deletion silently did nothing for
months. Unit tests missed it because they mocked the client, and a MagicMock
answers to any attribute you ask for.

This test reads the source instead: it cannot be fooled by a mock.
"""
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"

# Attribute calls on an object named `orion`/`client` that the SDK does not have.
FORBIDDEN = re.compile(r"\b(?:orion|orion_client)\.(get|delete|post|patch|put)\s*\(")


def _python_files():
    return sorted(p for p in APP.rglob("*.py"))


def test_no_module_calls_a_method_the_sdk_lacks():
    offenders = []
    for path in _python_files():
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith('"'):
                continue
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(APP)}:{i}: {stripped[:90]}")
    assert not offenders, (
        "Use get_entity / delete_entity / query_entities instead:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize("name", ["get_entity", "delete_entity", "query_entities"])
def test_the_methods_this_module_relies_on_exist(name):
    """Pin the SDK surface we depend on, so a future SDK bump fails loudly here."""
    from nkz_platform_sdk import SyncOrionClient

    assert hasattr(SyncOrionClient, name), f"SyncOrionClient lost {name}"
