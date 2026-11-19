"""Monitoring period and state of a parcel, from its crop timeline and its subscription.

Pure rules: the single place that decides when a parcel is watched. The crop comes
from the platform crop timeline (``nkz_platform_sdk``); a sowing or harvest counts
only when it is registered (``actual`` or ``declared``), never when it is planned.
"""
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.services.woody_species import is_tree_species

FACTS = ("actual", "declared")  # same facts as the SDK's crop_cycles
CONTINUOUS_WINDOW_DAYS = 365
DEFAULT_ACTIVATION_ROLES = "Farmer,TechnicalConsultant,TenantAdmin,PlatformAdmin"


# A woody crop is over when its own entity says so (the SDK does not see crops without a sowing).
ENDED_STATUSES = ("finished", "terminated", "uprooted", "cancelled")


def utc_today() -> date:
    """The one date basis of monitoring: today in UTC."""
    return datetime.now(timezone.utc).date()


@dataclass(frozen=True)
class Period:
    start: date
    end: Optional[date]
    kind: str  # "woody" | "annual" | "continuous"


def _fact_date(boundary: Optional[dict]) -> Optional[date]:
    """Date of a registered boundary, None when absent, planned or malformed."""
    if not isinstance(boundary, dict) or boundary.get("provenance") not in FACTS:
        return None
    try:
        return date.fromisoformat(str(boundary.get("date"))[:10])
    except ValueError:
        return None


def _cycle(timeline: Optional[dict]) -> Optional[dict]:
    """The current cycle, else the last one."""
    if not timeline:
        return None
    return timeline.get("current") or timeline.get("previous")


def _usable(cycle: Optional[dict]) -> bool:
    """A cycle with a registered sowing."""
    return cycle is not None and _fact_date(cycle.get("start")) is not None


def needs_parcel_crop(timeline: Optional[dict]) -> bool:
    """True when the timeline has no cycle with a registered sowing, so the parcel's own crop must be read."""
    return timeline is not None and not _usable(_cycle(timeline))


def _fallback_crop(timeline: Optional[dict], today: date) -> Optional[dict]:
    """The parcel's own crop when it is a woody crop still standing.

    Only woody crops: an annual with no registered sowing has no period. The SDK
    timeline never treats a crop without a sowing as current, and an orchard planted
    decades ago has no planting record.
    """
    crop = (timeline or {}).get("parcel_crop")
    if not crop or not is_woody(crop) or crop.get("status") in ENDED_STATUSES:
        return None
    end = _fact_date_any(crop.get("end"))
    if end is not None and end < today:
        return None
    return crop


def _fact_date_any(boundary: Optional[dict]) -> Optional[date]:
    """Date of a boundary whatever its provenance (the parcel's own crop carries only the entity's dates)."""
    if not isinstance(boundary, dict):
        return None
    try:
        return date.fromisoformat(str(boundary.get("date"))[:10])
    except ValueError:
        return None


def is_woody(cycle: dict) -> bool:
    """A perennial cycle, or one without a declared lifecycle whose species is a tree or vine."""
    lifecycle = cycle.get("lifecycle")
    if lifecycle == "perennial":
        return True
    if lifecycle in (None, "", "unknown"):
        return is_tree_species(cycle.get("species"))
    return False


def _stand_removed(cycle: dict, today: date) -> bool:
    """A perennial stand with a registered end (uprooted): the only end of a woody crop.

    A woody crop recognised by species only keeps going after any harvest.
    """
    end = _fact_date(cycle.get("end"))
    return cycle.get("lifecycle") == "perennial" and end is not None and end < today


def monitoring_period(timeline: Optional[dict], today: date, continuous: bool) -> Optional[Period]:
    """When the parcel should be watched, None when it should not."""
    cycle = _cycle(timeline)
    window_start = today - timedelta(days=CONTINUOUS_WINDOW_DAYS)
    if not _usable(cycle):
        if _fallback_crop(timeline, today) is not None:
            return Period(window_start, None, "woody")
        return None
    sowing = _fact_date(cycle.get("start"))
    if is_woody(cycle) and not _stand_removed(cycle, today):
        return Period(window_start, None, "woody")
    if continuous:
        # Continuity only lifts the harvest pause: an annual crop is still watched from its sowing.
        return Period(sowing, None, "continuous")
    if is_woody(cycle):
        return None
    return Period(sowing, _fact_date(cycle.get("end")), "annual")


def activation_roles() -> list[str]:
    raw = os.getenv("VEG_ACTIVATION_ROLES") or DEFAULT_ACTIVATION_ROLES
    return [role.strip() for role in raw.split(",") if role.strip()]


def _alive(cycle: Optional[dict], today: date) -> bool:
    """A sown annual not yet harvested, or a woody crop not removed."""
    if not _usable(cycle):
        return False
    if is_woody(cycle):
        return not _stand_removed(cycle, today)
    end = _fact_date(cycle.get("end"))
    return end is None or end >= today


def _monitoring_of(subscription: Any) -> str:
    if subscription is None:
        return "none"
    if subscription.is_active:
        return "active"
    return "paused_harvest" if subscription.paused_reason == "harvest" else "paused_user"


def _iso(value: Optional[date]) -> Optional[str]:
    return value.isoformat() if value else None


def monitoring_state(timeline: Optional[dict], subscription: Any, continuous_by: list[str], today: date) -> dict:
    """The parcel's crop, monitoring status and period (spec 5.2).

    The subscription decides the status: it is what actually downloads. A parcel
    activated in the core without a subscription reads as ``none`` and so needs
    activation; activating it again retries.
    """
    cycle = _cycle(timeline)
    period = monitoring_period(timeline, today, bool(continuous_by))
    monitoring = _monitoring_of(subscription)
    present = _alive(cycle, today)
    if not _usable(cycle):
        fallback = _fallback_crop(timeline, today)
        if fallback is not None:
            cycle, present = {**fallback, "start": {}, "end": {}}, True
    lifecycle = None
    if cycle is not None:
        lifecycle = "perennial" if is_woody(cycle) else cycle.get("lifecycle")
    return {
        "crop": {
            "present": present,
            "species": cycle.get("species") if cycle else None,
            "lifecycle": lifecycle,
            "planting_date": _iso(_fact_date(cycle.get("start"))) if cycle else None,
            "termination_date": _iso(_fact_date(cycle.get("end"))) if cycle else None,
        },
        "monitoring": monitoring,
        "period": {"start": _iso(period.start) if period else None, "end": _iso(period.end) if period else None},
        "needs_activation": present and monitoring == "none",
        "continuous_by": sorted(continuous_by),
        "activation_roles": activation_roles(),
    }
