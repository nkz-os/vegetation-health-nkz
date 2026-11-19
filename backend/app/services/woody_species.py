"""Woody (tree and vine) crop species, matched by name when the crop declares no lifecycle."""
import re
from typing import Optional

TREE_KEYWORDS = {
    # Latin genera
    "olea", "vitis", "prunus", "citrus", "malus", "pyrus", "juglans", "amygdalus", "corylus", "ficus", "punica",
    # EPPO codes used across the platform
    "olveu", "oleeu", "vitvi", "prndu", "prnav", "prnpe", "prnar", "citsi", "citlo", "citre", "citrs", "mabsd",
    "malpu", "pyuco", "iugre", "corav", "ficca",
    # common names (es, ca, eu, fr, pt, en)
    "olivo", "olivar", "olive", "olivier", "oliveira", "viña", "vid", "vinya", "mahats", "vigne", "vinha", "vine",
    "almendro", "ametller", "almond", "amandier", "amendoeira", "naranjo", "limonero", "cítricos", "citricos", "agrumes",
    "manzano", "pomera", "apple", "pommier", "macieira", "peral", "pear", "poirier", "nogal", "walnut", "noyer",
    "avellano", "hazel", "noisetier", "cerezo", "cherry", "melocotonero", "peach",
}


def is_tree_species(species: Optional[str]) -> bool:
    """True when any word of the species name is a known tree or vine crop."""
    return bool(species and set(re.findall(r"[^\W\d_]+", species.lower())) & TREE_KEYWORDS)
