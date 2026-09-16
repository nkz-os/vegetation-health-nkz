"""Engine routing — decide which engine serves a given index request.

Copernicus (Sentinel Hub Statistical API) serves every standard index; a tenant
custom formula or an unknown index still falls to the local engine.

NDRE used to be pinned to local to keep Sen2Res 10 m super-resolution, because
Copernicus serves the red-edge band at its native 20 m. That split meant the two
engines picked different scenes, so NDRE and the optical indices never shared a
sensing date: whenever an NDRE job finished with a newer date, every other layer
in the viewer went blank on a date it did not have. Owner decision 2026-09-16:
one engine and aligned dates are worth more than 10 m red-edge.

To put red-edge back on the local engine, move "NDRE" into LOCAL_ONLY — the
routing itself needs no change.

Pure function, no I/O — safe to call on the request hot path.
"""

# Indices Copernicus serves. NDRE is included at its native 20 m (see above).
COPERNICUS_ELIGIBLE = {"NDVI", "EVI", "SAVI", "GNDVI", "NDRE"}

# Indices forced onto the local engine. Empty by owner decision (2026-09-16).
LOCAL_ONLY: set[str] = set()


def route_index(index_type: str, has_custom_formula: bool) -> str:
    """Return the engine name for an index request: "local" or "copernicus".

    Rules (first match wins):
      1. custom formula                → "local"
      2. explicitly local-only (NDRE)  → "local"
      3. Copernicus-eligible           → "copernicus"
      4. anything else (unknown)       → "local" (safe default)
    """
    idx = (index_type or "").upper()
    if has_custom_formula:
        return "local"
    if idx in LOCAL_ONLY:
        return "local"
    if idx in COPERNICUS_ELIGIBLE:
        return "copernicus"
    return "local"
