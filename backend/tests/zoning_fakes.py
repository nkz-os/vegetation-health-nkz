"""An in-memory context broker for the zoning version tests."""
import copy
from urllib.parse import unquote

BROKER = "http://broker"
NKZ = "https://nkz-os.org/ns/"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return copy.deepcopy(self._body)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _plain(entity, name):
    v = entity.get(name)
    if isinstance(v, dict):
        return v.get("object", v.get("value"))
    return v


class FakeBroker:
    """Stores entities by id; ``session.get`` answers entity reads and the parcel zone listing."""

    def __init__(self, entities=()):
        self.context_broker_url = BROKER
        self.session = self
        self.store = {e["id"]: copy.deepcopy(e) for e in entities}
        self.refuse_create = False
        self.refuse_append = False
        self.refuse_delete_attr = False
        self.created, self.deleted, self.appended = [], [], []

    # --- session.get
    def get(self, url, params=None, headers=None, timeout=None):
        path = url[len(BROKER):]
        if path == "/ngsi-ld/v1/entities":
            rows = [
                {"id": e["id"], "zoningMethod": _plain(e, "zoningMethod"),
                 "zoningRunId": _plain(e, "zoningRunId"), "hasAgriParcel": _plain(e, "hasAgriParcel")}
                for e in self.store.values() if e.get("type") == params.get("type")
            ]
            return _Resp(200, rows)
        eid = unquote(path.rsplit("/", 1)[-1])
        if eid in self.store:
            return _Resp(200, self.store[eid])
        return _Resp(404, {})

    # --- FIWAREClient surface used by the zoning code
    def create_entity(self, entity):
        if self.refuse_create:
            return False
        self.store[entity["id"]] = copy.deepcopy(entity)
        self.created.append(entity["id"])
        return True

    def delete_entity(self, entity_id):
        self.deleted.append(entity_id)
        return self.store.pop(entity_id, None) is not None

    def append_attrs(self, entity_id, attrs):
        if self.refuse_append or entity_id not in self.store:
            return False
        self.store[entity_id].update(copy.deepcopy(attrs))
        self.appended.append((entity_id, sorted(attrs)))
        return True

    def delete_attr(self, entity_id, attr):
        if self.refuse_delete_attr:
            return False
        self.store.get(entity_id, {}).pop(attr, None)
        return True

    def run_ids(self):
        return {_plain(e, "zoningRunId") for e in self.store.values()}


PARCEL = "urn:ngsi-ld:AgriParcel:p1"
TENANT = "acme"
# Halves of a small square near (-1.65, 42.80).
WEST = {"type": "Polygon", "coordinates": [[[-1.6510, 42.8000], [-1.6495, 42.8000], [-1.6495, 42.8020],
                                            [-1.6510, 42.8020], [-1.6510, 42.8000]]]}
EAST = {"type": "Polygon", "coordinates": [[[-1.6495, 42.8000], [-1.6480, 42.8000], [-1.6480, 42.8020],
                                            [-1.6495, 42.8020], [-1.6495, 42.8000]]]}


def _p(value):
    return {"type": "Property", "value": value}


def zone(run_id, rank, level, geom, *, area=1.0, rel=100, mean=0.6, confirming=5, total=6,
         dates=("2026-04-01", "2026-05-01")):
    """An AgriManagementZone as the multi-date proposal publishes it."""
    return {
        "id": f"urn:ngsi-ld:AgriManagementZone:p1:{run_id[:8]}:L{rank}",
        "type": "AgriManagementZone",
        "hasAgriParcel": {"type": "Relationship", "object": PARCEL},
        "location": {"type": "GeoProperty", "value": geom},
        "zoneId": _p(rank), "zoneRank": _p(rank), "zoneName": _p(level), "zoneLevel": _p(level),
        "relativeVigorPct": _p(rel), "confirmingDates": _p(confirming), "totalDates": _p(total),
        "sourceDates": _p(list(dates)), "meanValue": _p(mean), "areaHa": _p(area),
        "variableAttribute": _p("NDVI"), "zoningRunId": _p(run_id), "zoningMethod": _p("vegetationIndexMultiDate"),
        "dateZoned": _p({"@type": "DateTime", "@value": "2026-11-16T09:00:00Z"}),
    }


def run(run_id):
    """Two zones: high on the west half, low on the east half."""
    return [zone(run_id, 1, "high", WEST, rel=112, mean=0.7), zone(run_id, 3, "low", EAST, rel=88, mean=0.5)]
