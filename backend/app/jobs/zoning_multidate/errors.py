"""Outcomes of technician actions on a zoning, mapped to HTTP statuses by the API."""


class ZoningNotFound(Exception):
    """404: the run or zone does not exist for this parcel."""


class ZoningConflict(Exception):
    """409: the run is no longer the current zoning nor the pending proposal."""


class ZoningInvalid(Exception):
    """422: the request does not fit this zoning (e.g. a level it does not have)."""


class BrokerUnavailable(Exception):
    """502: the context broker refused or failed; nothing was changed."""
