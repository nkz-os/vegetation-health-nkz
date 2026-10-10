"""Sentinel-2 L2A digital numbers to surface reflectance.

L2A stores reflectance as integers: rho = (DN + BOA_ADD_OFFSET) / QUANTIFICATION_VALUE.
From processing baseline 04.00 (January 2022) BOA_ADD_OFFSET is -1000 for every
band; before it there is no offset. DN 0 is NO_DATA. The baseline is part of the
product name (``_N0511_`` = 05.11), so it travels with every scene id.
"""

import re
from dataclasses import dataclass

_BASELINE = re.compile(r"_N(\d{2})(\d{2})_")
QUANTIFICATION_VALUE = 10000.0
BASELINE_WITH_OFFSET = 4.0
BOA_ADD_OFFSET = -1000.0


@dataclass(frozen=True)
class L2ARadiometry:
    offset: float
    quantification: float = QUANTIFICATION_VALUE


def l2a_radiometry(product_id: str) -> L2ARadiometry:
    """Radiometric parameters of an L2A product, from its processing baseline.

    An id without a baseline is rejected: guessing the offset would bias every index.
    """
    m = _BASELINE.search(product_id or "")
    if not m:
        raise ValueError(f"cannot read the processing baseline of product {product_id!r}")
    baseline = int(m.group(1)) + int(m.group(2)) / 100.0
    return L2ARadiometry(offset=BOA_ADD_OFFSET if baseline >= BASELINE_WITH_OFFSET else 0.0)
