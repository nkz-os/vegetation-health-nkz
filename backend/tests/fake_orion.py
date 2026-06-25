"""Fake async OrionClient mirroring the REAL SDK surface.

Deliberately exposes ONLY the methods the writers are allowed to call
(upsert_entities_batch + async context). It has NO .post/.patch, so any code
reverting to the old broken pattern raises AttributeError → the test fails.
"""
from typing import Any, Dict, List


class FakeAsyncOrion:
    last_instance = None

    def __init__(self, tenant_id: str):
        self.tenant_id = tenant_id
        self.calls: List[List[Dict[str, Any]]] = []
        self.entities: List[Dict[str, Any]] = []
        FakeAsyncOrion.last_instance = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def upsert_entities_batch(self, entities: List[Dict[str, Any]]) -> Dict[str, Any]:
        self.calls.append(entities)
        self.entities.extend(entities)
        return {"upserted": len(entities), "errors": [], "entity_ids": [e.get("id") for e in entities]}

    async def close(self):
        return None
