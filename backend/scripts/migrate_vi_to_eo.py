#!/usr/bin/env python3
"""Migrate VegetationIndex entities to EOProduct (one per index family present).

Runs INSIDE the vegetation-health-backend container (imports the app).

Dry-run:  python3 scripts/migrate_vi_to_eo.py --tenants montiko --dry-run
Apply:    python3 scripts/migrate_vi_to_eo.py --tenants montiko,other-tenant
"""

import argparse
import sys
from datetime import date, datetime

import requests

from app.services.fiware_integration import (
    CONTEXT_URL,
    INDEX_ATTRS,
    ORION_URL,
    upsert_eo_product,
)

PAGE = 200


def _get_headers(tenant_id: str) -> dict:
    return {
        "Accept": "application/json",
        "NGSILD-Tenant": tenant_id,
        "Fiware-Service": tenant_id,
        "Fiware-ServicePath": "/",
        "Link": (
            f'<{CONTEXT_URL}>; rel="http://www.w3.org/ns/json-ld#context";'
            ' type="application/ld+json"'
        ),
    }


def _iter_vegetation_indices(tenant_id: str):
    offset = 0
    while True:
        resp = requests.get(
            f"{ORION_URL}/ngsi-ld/v1/entities",
            params={"type": "VegetationIndex", "limit": PAGE, "offset": offset},
            headers=_get_headers(tenant_id),
            timeout=15,
        )
        resp.raise_for_status()
        page = resp.json()
        if not isinstance(page, list) or not page:
            return
        yield from page
        if len(page) < PAGE:
            return
        offset += PAGE


def _attr_value(entity: dict, name: str):
    attr = entity.get(name)
    if isinstance(attr, dict):
        return attr.get("value")
    return attr


def _sensing_date(entity: dict, attr_mean: str) -> date:
    attr = entity.get(attr_mean)
    observed = attr.get("observedAt") if isinstance(attr, dict) else None
    if observed:
        return datetime.fromisoformat(observed.replace("Z", "+00:00")).date()
    return date.today()


def migrate_tenant(tenant_id: str, dry_run: bool) -> dict:
    created, skipped, errors = 0, 0, 0
    for vi in _iter_vegetation_indices(tenant_id):
        parcel_ref = vi.get("hasAgriParcel") or vi.get("refAgriParcel") or {}
        parcel_urn = (
            parcel_ref.get("object") if isinstance(parcel_ref, dict) else parcel_ref
        )
        if not parcel_urn:
            print(f"  SKIP (no parcel relationship): {vi.get('id')}")
            skipped += 1
            continue

        raster_url = _attr_value(vi, "rasterUrl") or ""
        pixel_count = int(_attr_value(vi, "pixelCount") or 0)

        for product_type, (a_mean, a_min, a_max, a_std) in INDEX_ATTRS.items():
            mean = _attr_value(vi, a_mean)
            if mean is None:
                continue  # this index family is absent — do NOT zero-fill
            stats = {
                "mean": mean,
                "min": _attr_value(vi, a_min),
                "max": _attr_value(vi, a_max),
                "std": _attr_value(vi, a_std),
                "pixel_count": pixel_count,
            }
            stats = {k: v for k, v in stats.items() if v is not None}
            sensing = _sensing_date(vi, a_mean)

            if dry_run:
                created += 1
                print(f"  [DRY-RUN] {parcel_urn} -> EOProduct {product_type} @ {sensing}")
                continue

            result = upsert_eo_product(
                tenant_id=tenant_id,
                parcel_id=parcel_urn,
                product_type=product_type,
                statistics=stats,
                raster_url=raster_url or None,
                sensing_date=sensing,
                pixel_count=pixel_count,
            )
            if result:
                created += 1
                print(f"  UPSERTED: {result}")
            else:
                errors += 1
                print(f"  ERROR: {parcel_urn} {product_type}")
    return {"created": created, "skipped": skipped, "errors": errors}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tenants", required=True,
        help="Comma-separated tenant IDs",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    total = {"created": 0, "skipped": 0, "errors": 0}
    for tenant in [t.strip() for t in args.tenants.split(",") if t.strip()]:
        print(f"== Tenant: {tenant}")
        result = migrate_tenant(tenant, args.dry_run)
        for k in total:
            total[k] += result[k]
    print(f"\nDone: {total}")
    if args.dry_run:
        print("DRY-RUN — nothing written.")
    sys.exit(1 if total["errors"] else 0)


if __name__ == "__main__":
    main()
