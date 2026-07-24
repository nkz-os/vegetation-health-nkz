"""Engine routing — decide which engine serves a given index request.

Copernicus (Sentinel Hub Statistical API) is only used for indices that are
native at 10 m and carry no tenant custom formula. Red-edge (NDRE) MUST stay
on the legacy local engine to preserve the Sen2Res 10 m super-resolution —
Copernicus would serve the coarser 20 m native red-edge band. Any custom
formula, unknown index, or explicitly local-only index falls to local.

Pure function, no I/O — safe to call on the request hot path.
"""

# 10 m-native indices that Copernicus can serve at full resolution.
COPERNICUS_ELIGIBLE = {"NDVI", "EVI", "SAVI", "GNDVI"}

# Red-edge: 20 m native on Copernicus → keep local for Sen2Res 10 m super-res.
LOCAL_ONLY = {"NDRE"}


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
