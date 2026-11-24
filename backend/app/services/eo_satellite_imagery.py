"""Pure builders for the ``EOSatelliteImagery`` NGSI-LD entity.

One entity is the analysis of ONE satellite acquisition over ONE target (parcel
or management zone): (target, product family, UTC day). Everything the
analysis measured travels in the ``analytics`` list as fixed-decimal strings.

No broker code lives here: the body carries no ``@context`` (the SDK upsert
adds it) and relationships carry no ``"@type": "@id"``.

Identifier (contract section 2):
``urn:ngsi-ld:EOSatelliteImagery:{tenant}:{parcel|zone}:{targetId}:{family}:{day}``
where ``{targetId}`` is the target URN without its ``urn:ngsi-ld:{Type}:``
prefix, keeping every other segment and its ``:`` (no encoding). The last
segment alone is NOT enough: zone ids end in ``L1``, ``L2`` ... on every parcel.
"""
from __future__ import annotations

import logging
import math
import numbers
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
    def default_product_type(self) -> str | None:
        """The single product code the request itself fixes (Sentinel-2 L2A); None elsewhere."""
        return _PRODUCT_TYPE.get(self)


_ANALYSIS_TYPE = {
    Family.S2_INDICES: "vegetation-indices",
    Family.S2_COMPOSITE: "vegetation-indices-composite",
    Family.S1_BACKSCATTER: "sar-backscatter",
    Family.S1_SOIL_MOISTURE: "sar-soil-moisture",
    Family.S1_CHANGE: "sar-change-detection",
    Family.LANDSAT_LST: "land-surface-temperature",
    Family.CLMS_LST: "land-surface-temperature",
}

# productType when there is no concrete scene (Statistical API path) and the request itself fixes one code
# (contract section 4, "regla unica"). Every other family omits it: never a generic value put by hand.
_PRODUCT_TYPE = {
    Family.S2_INDICES: "S2MSI2A",
    Family.S2_COMPOSITE: "S2MSI2A",
}

# The writer must never write a family the reader does not know. (The equality
# between the two lists is asserted in the tests, not at import.)
_UNKNOWN_FAMILIES = [f.value for f in Family if f.value not in EO_FAMILIES]
if _UNKNOWN_FAMILIES:  # pragma: no cover
    raise ImportError(f"families unknown to nkz_platform_sdk.EO_FAMILIES: {_UNKNOWN_FAMILIES}")


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

INDEX_NAMES = ("ndvi", "evi", "savi", "osavi", "gndvi", "ndre", "ndmi")
_SAR_RASTER = ("backscatterVV", "backscatterVH")

# One explicit row per contract value: kind, decimals, raster (has a stored
# .tif and per-pixel statistics). A name the SDK knows but this table does not
# is a contract change and raises.
_CLASSES: dict[str, tuple[str, int | None, bool]] = {
    **{n: ("num", 6, True) for n in INDEX_NAMES},
    "lst": ("num", 2, True),
    "backscatterVV": ("num", 4, True),
    "backscatterVH": ("num", 4, True),
    "soilMoistureIndex": ("num", 4, True),
    "pixelCount": ("count1", None, False),
    "validPixelFraction": ("num", 4, False),
    "parcelCloudFraction": ("num", 4, False),
    "cloudCoverage": ("num", 2, False),
    "incidenceAngle": ("num", 2, False),
    "changeFlag": ("text", None, False),
    "changeConfidence": ("num", 4, False),
    "deltaVV": ("num", 4, False),
}
_RASTER_BASES = frozenset(n for n, c in _CLASSES.items() if c[2])
_STAT_SUFFIXES = frozenset({"min", "max", "std", "interiorMean", "interiorMedian", "interiorP10", "interiorP90"})
_INTERIOR_SUFFIXES = frozenset(
    {"interiorMean", "interiorMedian", "interiorP10", "interiorP90",
     "interiorPixelCount", "interiorValidFraction", "interiorMask"}
)
_MASK_VALUES = frozenset({"core", "parcel"})
_NEGATIVE_ZERO = re.compile(r"^-(0(\.0+)?)$")

# Primary values: a family with none of them has nothing to say.
_PRIMARY = {
    Family.S2_INDICES: frozenset(INDEX_NAMES),
    Family.S2_COMPOSITE: frozenset(INDEX_NAMES),
    Family.S1_BACKSCATTER: frozenset(_SAR_RASTER),
    Family.S1_SOIL_MOISTURE: frozenset({"soilMoistureIndex"}),
    Family.S1_CHANGE: frozenset({"changeFlag"}),
    Family.LANDSAT_LST: frozenset({"lst"}),
    Family.CLMS_LST: frozenset({"lst"}),
}


def _classify(name: str) -> tuple[str, int | None]:
    """Return (kind, decimals) for a contract name; ValueError if unknown.

    kind: "text", "mask", "count1" (whole >= 1), "count0" (whole >= 0), "num".
    """
    base, dot, suffix = name.partition(".")
    if base not in EO_VALUE_NAMES:
        raise ValueError(f"unknown analytics name: {name!r}")
    if base not in _CLASSES:
        raise ValueError(f"analytics name {name!r} has no class in this writer")
    kind, decimals, raster = _CLASSES[base]
    if not dot:
        return kind, decimals
    if not raster:
        raise ValueError(f"unknown analytics name: {name!r}")
    if suffix == "interiorMask":
        return "mask", None
    if suffix == "interiorPixelCount":
        return "count0", None
    if suffix == "interiorValidFraction":
        return "num", 4
    if suffix in _STAT_SUFFIXES:
        return "num", decimals
    raise ValueError(f"unknown analytics name: {name!r}")


def _usable(value: Any) -> bool:
    """False for None, empty text and non-finite numbers (omitted, never written)."""
    if value is None:
        return False
    if isinstance(value, str):
        return value != ""
    if isinstance(value, numbers.Real) and not isinstance(value, bool):
        return math.isfinite(float(value))
    return True  # wrong types are rejected by format_value


def format_value(name: str, value: float | int | str) -> str:
    """Contract section 5 representation: fixed decimals, never an exponent."""
    kind, decimals = _classify(name)
    if kind in ("text", "mask"):
        if not isinstance(value, str):
            raise ValueError(f"{name} takes text, got {type(value).__name__}")
        if kind == "mask" and value not in _MASK_VALUES:
            raise ValueError(f"{name} must be one of {sorted(_MASK_VALUES)}, got {value!r}")
        return value
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} takes a number, got {type(value).__name__}")
    if not math.isfinite(float(value)):
        raise ValueError(f"{name} is not finite")
    if kind in ("count1", "count0"):
        number = int(value) if isinstance(value, numbers.Integral) else float(value)
        if isinstance(number, float):
            if not number.is_integer():
                raise ValueError(f"{name} must be a whole number, got {value!r}")
            number = int(number)
        floor = 1 if kind == "count1" else 0
        if number < floor:
            raise ValueError(f"{name} must be >= {floor}, got {value!r}")
        return str(number)
    text = f"{float(value):.{decimals}f}"
    return _NEGATIVE_ZERO.sub(r"\1", text)


_S2 = frozenset({Family.S2_INDICES, Family.S2_COMPOSITE})
_S1 = frozenset({Family.S1_BACKSCATTER, Family.S1_SOIL_MOISTURE, Family.S1_CHANGE})
_BOTH_KINDS = frozenset({"parcel", "zone"})
_PARCEL_ONLY = frozenset({"parcel"})

# Contract section 5, column "Donde": name class -> (families, target kinds).
# Statistics (.min/.max/.std) follow their value; interior* has its own row.
_WHERE: dict[str, tuple[frozenset, frozenset]] = {
    **{n: (_S2, _BOTH_KINDS) for n in INDEX_NAMES},
    "lst": (frozenset({Family.LANDSAT_LST, Family.CLMS_LST}), _BOTH_KINDS),
    "backscatterVV": (frozenset({Family.S1_BACKSCATTER}), _BOTH_KINDS),
    "backscatterVH": (frozenset({Family.S1_BACKSCATTER}), _BOTH_KINDS),
    "soilMoistureIndex": (frozenset({Family.S1_SOIL_MOISTURE}), _BOTH_KINDS),
    "pixelCount": (frozenset(Family) - {Family.S1_CHANGE}, _BOTH_KINDS),
    "validPixelFraction": (_S2 | _S1, _BOTH_KINDS),
    "parcelCloudFraction": (frozenset({Family.S2_INDICES}), _PARCEL_ONLY),
    "cloudCoverage": (_S2, _BOTH_KINDS),
    "incidenceAngle": (_S1, _BOTH_KINDS),
    "changeFlag": (frozenset({Family.S1_CHANGE}), _BOTH_KINDS),
    "changeConfidence": (frozenset({Family.S1_CHANGE}), _BOTH_KINDS),
    "deltaVV": (frozenset({Family.S1_CHANGE}), _BOTH_KINDS),
    "interior": (frozenset({Family.S2_INDICES}), _PARCEL_ONLY),
}


def _check_scope(name: str, family: Family, kind: str) -> None:
    base, dot, suffix = name.partition(".")
    if dot and suffix in _INTERIOR_SUFFIXES:
        if base not in INDEX_NAMES:
            raise ValueError(f"{name} is only valid on an index")
        row = "interior"
    else:
        row = base
    if row not in _WHERE:
        raise ValueError(f"{name} has no scope row")
    families, kinds = _WHERE[row]
    if family not in families or kind not in kinds:
        raise ValueError(f"{name} is not allowed in {family.value} on a {kind} target")


# ── identifiers ─────────────────────────────────────────────────────────────


_URN_PREFIX = re.compile(r"^urn:ngsi-ld:[^:]+:(?P<rest>.*)$")
# Same character set as the reader's nkz_platform_sdk.eo._URN (a test compares them).
_URN_PATTERN = r"^urn:[A-Za-z0-9_.:~%+@/-]+$"
_URN = re.compile(_URN_PATTERN)


def _check_urn(urn: str, what: str) -> None:
    """Reject a URN the reader would drop: bad characters or an empty segment."""
    if not isinstance(urn, str) or not _URN.match(urn) or "::" in urn or urn.endswith(":"):
        raise ValueError(f"{what} is not a URN the reader accepts: {urn!r}")



def entity_id(tenant: str, target_kind: Literal["parcel", "zone"], target_urn: str, family: Family, day: date) -> str:
    if target_kind not in ("parcel", "zone"):
        raise ValueError(f"target kind must be parcel or zone, got {target_kind!r}")
    if not tenant or ":" in tenant:
        raise ValueError(f"tenant must be non-empty and contain no ':', got {tenant!r}")
    if isinstance(day, datetime) or not isinstance(day, date):
        raise ValueError("day must be a date, not a datetime")
    if not target_urn:
        raise ValueError("target urn is required")
    _check_urn(target_urn, "target urn")
    match = _URN_PREFIX.match(target_urn)
    if not match or not match.group("rest"):
        raise ValueError(f"target urn must be urn:ngsi-ld:<Type>:<id>, got {target_urn!r}")
    target_id = match.group("rest")
    return f"urn:ngsi-ld:{ENTITY_TYPE}:{tenant}:{target_kind}:{target_id}:{Family(family).value}:{day.isoformat()}"


# ── entity ──────────────────────────────────────────────────────────────────


def _prop(value: Any) -> dict:
    return {"type": "Property", "value": value}


def _geo(geometry: Mapping[str, Any]) -> dict:
    return {"type": "GeoProperty", "value": dict(geometry)}


def _relationship(urn: str) -> dict:
    return {"type": "Relationship", "object": urn}


def _check_target(target: Target) -> None:
    if not target.parcel_urn:
        raise ValueError("parcel_urn is required")
    _check_urn(target.parcel_urn, "parcel_urn")
    if target.zone_urn:
        _check_urn(target.zone_urn, "zone_urn")
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
    for name in sorted(analytics):
        value = analytics[name]
        _classify(name)  # unknown names fail even when their value is dropped
        _check_scope(name, family, target.kind)
        if _usable(value):
            items.append({"name": name, "value": format_value(name, value)})
    present = {i["name"] for i in items}

    if raster is not None:
        if not raster.local_server_path:
            raise ValueError("raster local_server_path is required")
        for layer_name, _ in raster.layers:
            if layer_name not in _RASTER_BASES:
                raise ValueError(f"raster layer {layer_name!r} is not a raster value name")
            if layer_name not in present:
                raise ValueError(f"raster layer {layer_name!r} is not in analytics")

    if len(items) < MIN_ANALYTICS or not (present & _PRIMARY[family]):
        logger.warning(
            "eo_satellite_imagery: not written, fewer than %d analytics or no primary value "
            "tenant=%s target=%s family=%s day=%s",
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
        if family.default_product_type:
            entity["productType"] = _prop(family.default_product_type)
    else:
        for key, value in (
            ("productID", scene.product_id),
            ("productURL", scene.product_url),
            ("productFormat", scene.product_format),
        ):
            if value:
                entity[key] = _prop(value)
        product_type = scene.product_type or family.default_product_type
        if product_type:
            entity["productType"] = _prop(product_type)
        # clms-lst has no platform, orbit or mode to report.
        carries_platform = family is not Family.CLMS_LST
        if scene.platform_name and carries_platform:
            entity["platformName"] = _prop(scene.platform_name)
        if scene.orbit_number is not None and carries_platform:
            if isinstance(scene.orbit_number, bool) or not isinstance(scene.orbit_number, numbers.Integral):
                raise ValueError(f"orbit_number must be an integer, got {scene.orbit_number!r}")
            entity["orbitNumber"] = _prop(int(scene.orbit_number))
        if scene.operational_mode and carries_platform:
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
