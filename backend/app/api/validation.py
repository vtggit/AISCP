"""Shared input validation for the API routers.

The checks every router applies to caller-controlled text before it
may reach the database.  Today the NUL-byte guard, which the
eleven routers each carried as a private copy (issue #1621).
"""

from fastapi import HTTPException

_NUL = chr(0)


def reject_nul_bytes(*values: str | None, detail: str) -> None:
    """Raise a 422 ``HTTPException`` carrying ``detail`` when any value
    given, that is not None, holds a NUL byte; otherwise return None.
    """
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(status_code=422, detail=detail)
    return None
