"""Copy EOProduct attributes kept under old IRIs to their official IRIs, verify, then drop the old copy.

    python -m app.jobs.eo_official_iri_expand [--tenant T ...] [--apply] [--verify] [--contract]

Step (a), default: for each EOProduct and term below that has the old key and lacks the official one,
add the official key with the same value, leaving the old one. Without --apply it only counts.
--verify: per tenant and term, totals with the old key, with the official key, missing (official
absent) and differing (both present, different value). Differing values are listed, never fixed.
Step (c), --contract: delete the old copy only on entities that already hold the official one with
the SAME value; refuses unless --verify is clean (0 missing, 0 differing) for the tenant.
Without --apply it only counts.

Entities are read WITHOUT a Link header: SDM and `nkz:` terms come back as full IRIs, default
vocabulary terms (productType, hasAgriParcelZone) come back short. That tells old from official
without depending on the @context mapping, which changes between (a) and (c). Old copies are
always deleted by full IRI, since after the mapping change the short name expands to the official copy.
"""
import argparse
import logging
import sys
from dataclasses import dataclass
from typing import Callable, Optional

from app.jobs.band_copy_cleanup import _tenant_paths

logger = logging.getLogger(__name__)

PAGE = 1000
DEFAULT_CTX = "https://uri.etsi.org/ngsi-ld/default-context/"
SDM = "https://smartdatamodels.org/dataModel.SatelliteImagery/"
NKZ = "https://nkz-os.org/ns/"

EXIT_OK = 0
EXIT_ERRORS = 1  # append/delete errors, or verify not clean
EXIT_REFUSED = 2  # --contract refused


@dataclass(frozen=True)
class Term:
    name: str
    read_old: str  # key of the old copy as read without Link
    delete_old: str  # full IRI used to delete the old copy
    official: str
    kind: str  # "Property" | "Relationship"


TERMS = (
    Term("sensingDate", NKZ + "sensingDate", NKZ + "sensingDate", SDM + "sensingDate", "Property"),
    Term("productType", "productType", DEFAULT_CTX + "productType", SDM + "productType", "Property"),
    Term("hasAgriParcelZone", "hasAgriParcelZone", DEFAULT_CTX + "hasAgriParcelZone",
         NKZ + "hasAgriParcelZone", "Relationship"),
)


def _payload(attr) -> object:
    return attr.get("object") if attr.get("type") == "Relationship" else attr.get("value")


def read_eo_products(client) -> list:
    """All EOProduct of the client's tenant, read without the Link header, paginated."""
    headers = client._headers("none")  # tenant headers only: no Link, no Content-Type
    entities, offset = [], 0
    while True:
        resp = client._session.get(
            client._url("/ngsi-ld/v1/entities"),
            params={"type": "EOProduct", "limit": PAGE, "offset": offset},
            headers=headers, timeout=getattr(client, "timeout", 30),
        )
        resp.raise_for_status()
        page = resp.json()
        entities.extend(page)
        if len(page) < PAGE:
            return entities
        offset += PAGE


def _attr(entity: dict, key: str) -> Optional[dict]:
    a = entity.get(key)
    return a if isinstance(a, dict) else None


def expand(client, entities: list, apply: bool) -> dict:
    """Step (a). Returns counts: entities to expand/expanded, attributes, errors."""
    result = {"entities": 0, "attrs": 0, "errors": 0}
    for entity in entities:
        to_add = {}
        for t in TERMS:
            old, off = _attr(entity, t.read_old), entity.get(t.official)
            if old is None or off is not None:
                continue
            if t.kind == "Relationship":
                to_add[t.official] = {"type": "Relationship", "object": old.get("object")}
            else:
                to_add[t.official] = {"type": "Property", "value": old.get("value")}
        if not to_add:
            continue
        result["entities"] += 1
        result["attrs"] += len(to_add)
        if not apply:
            continue
        try:
            client.append_entity_attrs(entity["id"], to_add, overwrite=False)
            logger.info("official IRI added tenant=%s entity=%s attrs=%d", client.tenant_id, entity["id"], len(to_add))
        except Exception as exc:  # a 400/207 is an error of this entity, never taken as done
            result["errors"] += 1
            logger.error("append failed tenant=%s entity=%s error=%s", client.tenant_id, entity["id"], exc)
    return result


def verify(entities: list) -> dict:
    """term name -> {old, official, missing, differing: [entity ids]}."""
    rep = {t.name: {"old": 0, "official": 0, "missing": 0, "differing": []} for t in TERMS}
    for entity in entities:
        for t in TERMS:
            old, off = _attr(entity, t.read_old), _attr(entity, t.official)
            r = rep[t.name]
            if old is not None:
                r["old"] += 1
            if off is not None:
                r["official"] += 1
            if old is not None and off is None:
                r["missing"] += 1
            elif old is not None and off is not None and _payload(old) != _payload(off):
                r["differing"].append(entity["id"])
    return rep


def _is_clean(rep: dict) -> bool:
    return all(r["missing"] == 0 and not r["differing"] for r in rep.values())


def _print_verify(tenant_id: str, rep: dict, out) -> None:
    for name, r in rep.items():
        out.write(f"{tenant_id}\t{name}\told={r['old']}\tofficial={r['official']}\t"
                  f"missing={r['missing']}\tdiffering={len(r['differing'])}\n")
        for eid in r["differing"]:
            out.write(f"DIFFERING\t{tenant_id}\t{eid}\t{name}\n")


def contract(client, entities: list, apply: bool) -> dict:
    """Step (c): delete the old copy where the official one is present with the same value."""
    result = {"attrs": 0, "errors": 0}
    for entity in entities:
        for t in TERMS:
            old, off = _attr(entity, t.read_old), _attr(entity, t.official)
            if old is None or off is None or _payload(old) != _payload(off):
                continue
            result["attrs"] += 1
            if not apply:
                continue
            try:
                client.delete_entity_attr(entity["id"], t.delete_old)
                logger.info("old copy deleted tenant=%s entity=%s attr=%s", client.tenant_id, entity["id"], t.delete_old)
            except Exception as exc:
                result["errors"] += 1
                logger.error("delete failed tenant=%s entity=%s attr=%s error=%s",
                             client.tenant_id, entity["id"], t.delete_old, exc)
    return result


def _default_client(tenant_id: str):
    from nkz_platform_sdk import SyncOrionClient
    return SyncOrionClient(tenant_id)


def main(argv=None, db=None, client_factory: Optional[Callable] = None, out=None) -> int:
    parser = argparse.ArgumentParser(description="Copy EOProduct attributes to their official IRIs.")
    parser.add_argument("--tenant", action="append", help="limit to this tenant (repeatable)")
    parser.add_argument("--apply", action="store_true", help="write (default: only count)")
    parser.add_argument("--verify", action="store_true", help="report old/official/missing/differing per term")
    parser.add_argument("--contract", action="store_true", help="delete old copies already held officially")
    args = parser.parse_args(argv)
    out = out or sys.stdout
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    own_db = db is None
    if own_db:
        from app import database  # imported late: needs DATABASE_URL, which tests do not set
        db = database.SessionLocal()
    factory = client_factory or _default_client
    rc = EXIT_OK
    try:
        for tenant_id in sorted(_tenant_paths(db, args.tenant)):
            client = factory(tenant_id)
            entities = read_eo_products(client)
            if args.contract:
                rep = verify(entities)
                _print_verify(tenant_id, rep, out)
                if not _is_clean(rep):
                    out.write(f"REFUSED\t{tenant_id}\tverify is not clean (missing or differing > 0)\n")
                    logger.error("contract refused tenant=%s", tenant_id)
                    return EXIT_REFUSED
                res = contract(client, entities, args.apply)
                verb = "deleted" if args.apply else "would delete"
                out.write(f"{tenant_id}\tcontract\t{verb}={res['attrs']}\terrors={res['errors']}\n")
                if res["errors"]:
                    rc = EXIT_ERRORS
                continue
            if args.apply or not args.verify:
                res = expand(client, entities, args.apply)
                verb = "added" if args.apply else "would add"
                out.write(f"{tenant_id}\texpand\t{verb}\tentities={res['entities']}\tattrs={res['attrs']}\t"
                          f"errors={res['errors']}\n")
                if res["errors"]:
                    rc = EXIT_ERRORS
                if args.apply and res["attrs"]:
                    entities = read_eo_products(client)
            if args.verify:
                rep = verify(entities)
                _print_verify(tenant_id, rep, out)
                if not _is_clean(rep):
                    rc = EXIT_ERRORS
        return rc
    finally:
        if own_db:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
