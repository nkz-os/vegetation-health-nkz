"""Copy EOProduct attributes kept under old IRIs to their official IRIs, verify, then drop the old copy.

    python -m app.jobs.eo_official_iri_expand [--tenant T ...] [--apply] [--verify] [--contract]

Step (a), default: for each EOProduct and term below that has the old key and lacks the official one,
add the official key with the same value, leaving the old one. Without --apply it only counts.
--verify: per tenant and term, totals with the old key, with the official key, missing (official
absent) and differing (both present, different value). Differing values are listed, never fixed.
Step (c), --contract: delete the old copy only on entities that already hold the official one with
the SAME value; refuses unless --verify is clean (0 missing, 0 differing) for the tenant.
Without --apply it only counts.

The read asks for the FULL type IRI (a short type without Link expands to the default vocabulary and
returns nothing) and is cross-checked against the broker's result count; a mismatch, or a read error,
fails that tenant only (exit 1, nothing written). A tenant with 0 entities is never reported clean.
An old copy that is a list or lacks value/object is counted `malformed`, never sent, and fails verify.
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


ABSENT, OK, MALFORMED = "absent", "ok", "malformed"
EOPRODUCT_TYPE = SDM + "EOProduct"  # full IRI: without the Link a short type expands to the default vocabulary (0 hits)
COUNT_HEADER = "NGSILD-Results-Count"


class ReadError(Exception):
    """The read of a tenant is not trustworthy; nothing is written for it."""


def _state(entity: dict, key: str, kind: str):
    """(state, payload) of one attribute: the single presence rule for expand, verify and contract.

    ok = a single attribute object with a non-null value/object; malformed = a list (multi-instance),
    a non-object, or an object without value/object (sending it would copy null, a 400).
    """
    if key not in entity:
        return ABSENT, None
    a = entity[key]
    field = "object" if kind == "Relationship" else "value"
    if not isinstance(a, dict) or a.get(field) is None:
        return MALFORMED, None
    return OK, a[field]


def read_eo_products(client) -> list:
    """All EOProduct of the client's tenant, read without the Link header, paginated.

    Cross-checked against the broker's result count: a mismatch raises ReadError.
    """
    headers = client._headers("none")  # tenant headers only: no Link, no Content-Type
    entities, offset, expected = [], 0, None
    while True:
        params = {"type": EOPRODUCT_TYPE, "limit": PAGE, "offset": offset}
        if offset == 0:
            params["count"] = "true"
        resp = client._session.get(
            client._url("/ngsi-ld/v1/entities"), params=params,
            headers=headers, timeout=getattr(client, "timeout", 30),
        )
        resp.raise_for_status()
        page = resp.json()
        if offset == 0:
            raw = (getattr(resp, "headers", None) or {}).get(COUNT_HEADER)
            try:
                expected = int(raw)
            except (TypeError, ValueError):
                raise ReadError(f"missing or invalid {COUNT_HEADER} header: {raw!r}")
        entities.extend(page)
        if len(page) < PAGE:
            break
        offset += PAGE
    if len(entities) != expected:
        raise ReadError(f"read {len(entities)} entities but the broker counts {expected}")
    return entities


def expand(client, entities: list, apply: bool) -> dict:
    """Step (a). Returns counts: entities to expand/expanded, attributes, errors, malformed skipped."""
    result = {"entities": 0, "attrs": 0, "errors": 0, "malformed": 0}
    for entity in entities:
        to_add = {}
        for t in TERMS:
            old_state, old_val = _state(entity, t.read_old, t.kind)
            off_state, _ = _state(entity, t.official, t.kind)
            if old_state == MALFORMED:
                result["malformed"] += 1
                logger.error("malformed old attribute tenant=%s entity=%s term=%s",
                             client.tenant_id, entity["id"], t.name)
                continue
            if old_state == ABSENT or off_state != ABSENT:
                continue
            if t.kind == "Relationship":
                to_add[t.official] = {"type": "Relationship", "object": old_val}
            else:
                to_add[t.official] = {"type": "Property", "value": old_val}
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
    """term name -> {old, official, missing, malformed, differing: [entity ids]}."""
    rep = {t.name: {"old": 0, "official": 0, "missing": 0, "malformed": 0, "differing": []} for t in TERMS}
    for entity in entities:
        for t in TERMS:
            old_state, old_val = _state(entity, t.read_old, t.kind)
            off_state, off_val = _state(entity, t.official, t.kind)
            r = rep[t.name]
            r["old"] += old_state != ABSENT
            r["official"] += off_state != ABSENT
            r["malformed"] += (old_state == MALFORMED) + (off_state == MALFORMED)
            if old_state == OK and off_state == ABSENT:
                r["missing"] += 1
            elif old_state == OK and off_state == OK and old_val != off_val:
                r["differing"].append(entity["id"])
    return rep


def _is_clean(rep: dict, entity_count: int) -> bool:
    """Clean = non-empty read and no missing, differing or malformed. An empty read proves nothing."""
    return entity_count > 0 and all(
        r["missing"] == 0 and r["malformed"] == 0 and not r["differing"] for r in rep.values())


def _print_verify(tenant_id: str, rep: dict, entity_count: int, out) -> None:
    if entity_count == 0:
        out.write(f"EMPTY\t{tenant_id}\tno EOProduct read; not a clean verify\n")
    for name, r in rep.items():
        out.write(f"{tenant_id}\t{name}\told={r['old']}\tofficial={r['official']}\t"
                  f"missing={r['missing']}\tdiffering={len(r['differing'])}\tmalformed={r['malformed']}\n")
        for eid in r["differing"]:
            out.write(f"DIFFERING\t{tenant_id}\t{eid}\t{name}\n")


def contract(client, entities: list, apply: bool) -> dict:
    """Step (c): delete the old copy where the official one is present with the same value."""
    result = {"attrs": 0, "errors": 0}
    for entity in entities:
        for t in TERMS:
            old_state, old_val = _state(entity, t.read_old, t.kind)
            off_state, off_val = _state(entity, t.official, t.kind)
            if old_state != OK or off_state != OK or old_val != off_val:
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
    errors, refused = False, False
    try:
        for tenant_id in sorted(_tenant_paths(db, args.tenant)):
            try:
                outcome = _run_tenant(tenant_id, factory, args, out)
            except Exception:  # one tenant's failure never stops the others
                logger.exception("tenant failed tenant=%s", tenant_id)
                out.write(f"ERROR\t{tenant_id}\tread or write failed\n")
                outcome = EXIT_ERRORS
            errors |= outcome == EXIT_ERRORS
            refused |= outcome == EXIT_REFUSED
        return EXIT_REFUSED if refused else EXIT_ERRORS if errors else EXIT_OK
    finally:
        if own_db:
            db.close()


def _run_tenant(tenant_id: str, factory, args, out) -> int:
    client = factory(tenant_id)
    entities = read_eo_products(client)  # ReadError propagates: nothing is written for this tenant
    rc = EXIT_OK
    out.write(f"{tenant_id}\tentities={len(entities)}\n")
    if args.contract:
        rep = verify(entities)
        _print_verify(tenant_id, rep, len(entities), out)
        if not _is_clean(rep, len(entities)):
            out.write(f"REFUSED\t{tenant_id}\tverify is not clean (empty, missing, differing or malformed)\n")
            logger.error("contract refused tenant=%s", tenant_id)
            return EXIT_REFUSED
        res = contract(client, entities, args.apply)
        verb = "deleted" if args.apply else "would delete"
        out.write(f"{tenant_id}\tcontract\t{verb}={res['attrs']}\terrors={res['errors']}\n")
        return EXIT_ERRORS if res["errors"] else EXIT_OK
    if args.apply or not args.verify:
        res = expand(client, entities, args.apply)
        verb = "added" if args.apply else "would add"
        out.write(f"{tenant_id}\texpand\t{verb}\tentities={res['entities']}\tattrs={res['attrs']}\t"
                  f"errors={res['errors']}\tmalformed={res['malformed']}\n")
        if res["errors"] or res["malformed"]:
            rc = EXIT_ERRORS
        if args.apply and res["attrs"]:
            entities = read_eo_products(client)
    if args.verify:
        rep = verify(entities)
        _print_verify(tenant_id, rep, len(entities), out)
        if not _is_clean(rep, len(entities)):
            rc = EXIT_ERRORS
    return rc


if __name__ == "__main__":
    sys.exit(main())
