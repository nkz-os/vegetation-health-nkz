"""Pure builders for the ``EOSatelliteImagery`` NGSI-LD entity.

One entity is the analysis of ONE satellite acquisition over ONE target (parcel
or management zone): (target, product family, UTC day). Everything the
analysis measured travels in the ``analytics`` list as fixed-decimal strings.

No broker code lives here: the body carries no ``@context`` (the SDK upsert
adds it) and relationships carry no ``"@type": "@id"``.
"""
from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any, Literal, Mapping, Sequence

from nkz_platform_sdk import EO_FAMILIES, EO_VALUE_NAMES

logger = logging.getLogger(__name__)

ENTITY_TYPE = "EOSatelliteImagery"
PROVIDER = "Nekazari vegetation module"
ZONE_RELATION = "hasAgriManagementZone"
MIN_ANALYTICS = 2  # Orion-LD flattens a one-element list to an object


class Family(str, Enum):
    S2_INDICES = "s2-indices"
    S2_COMPOSITE = "s2-composite"
    S1_BACKSCATTER = "s1-backscatter"
    S1_SOIL_MOISTURE = "s1-soil-moisture"
    S1_CHANGE = "s1-change"
    LANDSAT_LST = "landsat-lst"
    CLMS_LST = "clms-lst"

    @property
    def analysis_type(self) -> str:
        return _ANALYSIS_TYPE[self]

    @property
    def default_product_type(self) -> str:
        return _PRODUCT_TYPE[self]


_ANALYSIS_TYPE = {
    Family.S2_INDICES: "vegetation-indices",
    Family.S2_COMPOSITE: "vegetation-indices-composite",
    Family.S1_BACKSCATTER: "sar-backscatter",
    Family.S1_SOIL_MOISTURE: "sar-soil-moisture",
    Family.S1_CHANGE: "sar-change-detection",
    Family.LANDSAT_LST: "land-surface-temperature",
    Family.CLMS_LST: "land-surface-temperature",
}

# productType when there is no concrete scene (Statistical API path).
# ASSUMPTION: the CLMS value is not fixed by the contract; "CLMS-LST" keeps it
# distinct from landsat-lst (both share analysisType) - confirm with core.
_PRODUCT_TYPE = {
    Family.S2_INDICES: "S2MSI2A",
    Family.S2_COMPOSITE: "S2MSI2A",
    Family.S1_BACKSCATTER: "GRD",
    Family.S1_SOIL_MOISTURE: "GRD",
    Family.S1_CHANGE: "GRD",
    Family.LANDSAT_LST: "L2SP",
    Family.CLMS_LST: "CLMS-LST",
}

# The enum is the contract table; the SDK reader is the single source of names.
if tuple(f.value for f in Family) != tuple(EO_FAMILIES):  # pragma: no cover
    raise ImportError("Family enum out of sync with nkz_platform_sdk.EO_FAMILIES")


@dataclass(frozen=True)
class Target:
    kind: Literal["parcel", "zone"]
    parcel_urn: str
    zone_urn: str | None
    zone_relation: str | None
    geometry: Mapping[str, Any] | None


@dataclass(frozen=True)
class Sensing:
    """Sensing day (id and ``sensingDate`` day) and the real acquisition time, if any."""

    day: date
    time_utc: datetime | None = None


@dataclass(frozen=True)
class SceneMeta:
    product_id: str
    product_url: str
    product_format: str
    product_type: str
    platform_name: str
    orbit_number: int | None = None
    operational_mode: str | None = None
    footprint: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class RasterMeta:
    local_server_path: str
    layers: Sequence[tuple[str, str]]  # (analytics name, values explanation)


# ── value formatting ────────────────────────────────────────────────────────

_INDEX_DECIMALS = 6
_DECIMALS = {
    "backscatterVV": 4,
    "backscatterVH": 4,
    "deltaVV": 4,
    "lst": 2,
    "soilMoistureIndex": 4,
    "validPixelFraction": 4,
    "parcelCloudFraction": 4,
    "changeConfidence": 4,
    "cloudCoverage": 2,
    "incidenceAngle": 2,
}
_INTEGER_BASES = frozenset({"pixelCount"})
_TEXT_BASES = frozenset({"changeFlag"})
# Values that never carry per-pixel statistics.
_NO_STATS = frozenset(
    {"pixelCount", "validPixelFraction", "parcelCloudFraction", "cloudCoverage",
     "incidenceAngle", "changeFlag", "changeConfidence", "deltaVV"}
)
_STAT_SUFFIXES = frozenset({"min", "max", "std", "interiorMean", "interiorMedian", "interiorP10", "interiorP90"})
_NEGATIVE_ZERO = re.compile(r"^-(0(\.0+)?)$")


def _classify(name: str) -> tuple[str, int | None]:
    """Return ("text"|"int"|"num", decimals) for a contract name; ValueError if unknown."""
    base, dot, suffix = name.partition(".")
    if base not in EO_VALUE_NAMES:
        raise ValueError(f"unknown analytics name: {name!r}")
    if not dot:
        if base in _TEXT_BASES:
            return "text", None
        if base in _INTEGER_BASES:
            return "int", None
        return "num", _DECIMALS.get(base, _INDEX_DECIMALS)
    if base in _NO_STATS:
        raise ValueError(f"unknown analytics name: {name!r}")
    if suffix == "interiorMask":
        return "text", None
    if suffix == "interiorPixelCount":
        return "int", None
    if suffix == "interiorValidFraction":
        return "num", 4
    if suffix in _STAT_SUFFIXES:
        return "num", _DECIMALS.get(base, _INDEX_DECIMALS)
    raise ValueError(f"unknown analytics name: {name!r}")


def _usable(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    return not (isinstance(value, float) and not math.isfinite(value))


def format_value(name: str, value: float | int | str) -> str:
    """Contract section 5 representation: fixed decimals, never an exponent."""
    kind, decimals = _classify(name)
    if kind == "text":
        if not isinstance(value, str):
            raise ValueError(f"{name} takes text, got {type(value).__name__}")
        return value
    if isinstance(value, (str, bool)) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} takes a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ValueError(f"{name} is not finite")
    text = f"{value:.0f}" if kind == "int" else f"{value:.{decimals}f}"
    return _NEGATIVE_ZERO.sub(r"\1", text)


# ── identifiers ─────────────────────────────────────────────────────────────


def entity_id(tenant: str, target_kind: Literal["parcel", "zone"], target_urn: str, family: Family, day: date) -> str:
    if target_kind not in ("parcel", "zone"):
        raise ValueError(f"target kind must be parcel or zone, got {target_kind!r}")
    target_id = target_urn.rsplit(":", 1)[-1]
    if not tenant or not target_id:
        raise ValueError("tenant and target urn are required")
    return f"urn:ngsi-ld:{ENTITY_TYPE}:{tenant}:{target_kind}:{target_id}:{Family(family).value}:{day.isoformat()}"


# ── entity ──────────────────────────────────────────────────────────────────


def _prop(value: Any) -> dict:
    return {"type": "Property", "value": value}


def _geo(geometry: Mapping[str, Any]) -> dict:
    return {"type": "GeoProperty", "value": dict(geometry)}


def _relationship(urn: str) -> dict:
    return {"type": "Relationship", "object": urn}


def _check_target(target: Target) -> None:
    if target.kind == "parcel":
        if target.zone_urn is not None or target.zone_relation is not None:
            raise ValueError("a parcel target carries no zone")
    elif target.kind == "zone":
        if not target.zone_urn:
            raise ValueError("a zone target needs zone_urn")
        if target.zone_relation != ZONE_RELATION:
            raise ValueError(f"the only zone relation is {ZONE_RELATION}, got {target.zone_relation!r}")
    else:
        raise ValueError(f"target kind must be parcel or zone, got {target.kind!r}")


def _sensing_date(family: Family, sensing: Sensing) -> str:
    moment = sensing.time_utc
    if moment is None:
        return sensing.day.isoformat()
    if family is Family.S2_COMPOSITE:
        raise ValueError("a composite has no single acquisition time")
    if moment.tzinfo is None:
        raise ValueError("sensing time must be timezone-aware UTC")
    moment = moment.astimezone(timezone.utc).replace(microsecond=0)
    if moment.date() != sensing.day:
        raise ValueError("sensing time is not on the sensing day")
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def build_entity(
    *,
    tenant: str,
    target: Target,
    family: Family,
    sensing: Sensing,
    analytics: Mapping[str, float | int | str | None],
    scene: SceneMeta | None,
    raster: RasterMeta | None,
    result_description: str,
) -> dict | None:
    """Build the entity body, or None when fewer than two analytics remain."""
    family = Family(family)
    _check_target(target)
    target_urn = target.zone_urn if target.kind == "zone" else target.parcel_urn
    eid = entity_id(tenant, target.kind, target_urn, family, sensing.day)
    sensing_text = _sensing_date(family, sensing)

    items = []
    for name, value in analytics.items():
        _classify(name)  # unknown names fail even when their value is dropped
        if _usable(value):
            items.append({"name": name, "value": format_value(name, value)})

    if raster is not None:
        present = {i["name"] for i in items}
        for layer_name, _ in raster.layers:
            if layer_name not in present:
                raise ValueError(f"raster layer {layer_name!r} is not in analytics")

    if len(items) < MIN_ANALYTICS:
        logger.warning(
            "eo_satellite_imagery: not written, fewer than %d analytics tenant=%s target=%s family=%s day=%s",
            MIN_ANALYTICS, tenant, target_urn, family.value, sensing.day.isoformat(),
        )
        return None

    entity: dict[str, Any] = {
        "id": eid,
        "type": ENTITY_TYPE,
        "analysisType": _prop(family.analysis_type),
        "provider": _prop(PROVIDER),
        "sensingDate": _prop(sensing_text),
        "resultDescription": _prop(result_description),
        "analytics": _prop(items),
        "hasAgriParcel": _relationship(target.parcel_urn),
    }
    if target.kind == "zone":
        entity[ZONE_RELATION] = _relationship(target.zone_urn)
    if target.geometry:
        entity["processedBbox"] = _geo(target.geometry)

    if scene is None:
        entity["productType"] = _prop(family.default_product_type)
    else:
        entity["productID"] = _prop(scene.product_id)
        entity["productURL"] = _prop(scene.product_url)
        entity["productFormat"] = _prop(scene.product_format)
        entity["productType"] = _prop(scene.product_type)
        entity["platformName"] = _prop(scene.platform_name)
        if scene.orbit_number is not None:
            entity["orbitNumber"] = _prop(int(scene.orbit_number))
        if scene.operational_mode:
            entity["operationalMode"] = _prop(scene.operational_mode)
        if scene.footprint:
            entity["productBbox"] = _geo(scene.footprint)

    if raster is not None:
        entity["localServerPath"] = _prop(raster.local_server_path)
        entity["storageFormat"] = _prop("GeoTIFF")
        if len(raster.layers) >= 2:
            entity["contentInformation"] = _prop(
                [
                    {"layer_name": n, "layer_categorization": "numerical", "values_explanation": [expl]}
                    for n, expl in raster.layers
                ]
            )
    return entity
