"""Fake async OrionClient mirroring the REAL deployed SDK surface.

The real async OrionClient (nkz-platform-sdk 0.5.x) exposes
`upsert_entities_batch` + `close` (both coroutines) and is constructed as
`OrionClient(tenant_id, base_url=None, context_url=None)`. It does NOT support
the async context-manager protocol (no `__aenter__`/`__aexit__`) — so this fake
deliberately OMITS them. If the writer reverts to `async with OrionClient(...)`,
it fails here with the same error as production ("does not support the
asynchronous context manager protocol"), and it has no `.post`/`.patch` so the
original AttributeError bug is caught too.
"""
from typing import Any, Dict, List


class FakeAsyncOrion:
    last_instance = None

    def __init__(self, tenant_id: str, *args: Any, **kwargs: Any):
        self.tenant_id = tenant_id
        self.calls: List[List[Dict[str, Any]]] = []
        self.entities: List[Dict[str, Any]] = []
        self.closed = False
        FakeAsyncOrion.last_instance = self

    async def upsert_entities_batch(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.calls.append(entities)
        self.entities.extend(entities)
        return {"upserted": len(entities), "errors": [], "entity_ids": [e.get("id") for e in entities]}

    async def close(self) -> None:
        self.closed = True
