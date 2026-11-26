"""Shared input validation for the API routers.

The checks every router applies to caller-controlled text before it
may reach the database: the NUL-byte guard, which the eleven routers
each carried as a private copy (issue #1621), and the check that a
token subject is storable in a String(255) column.
"""

from typing import Self

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError, model_validator

from app.auth.models import AuthUser

_NUL = chr(0)


def reject_nul_bytes(*values: str | None, detail: str) -> None:
    """Raise a 422 ``HTTPException`` carrying ``detail`` when any value
    given, that is not None, holds a NUL byte; otherwise return None.
    """
    for value in values:
        if value is not None and _NUL in value:
            raise HTTPException(status_code=422, detail=detail)
    return None


class _StorableSubject(BaseModel):
    value: str = Field(max_length=255)

    @model_validator(mode="after")
    def _no_nul(self) -> Self:
        if _NUL in self.value:
            raise ValueError("must not contain NUL bytes")
        return self


def require_storable_subject(user: AuthUser) -> None:
    """The token subject is stored in a String(255) decided_by,
    adopted_by or designated_by column; a hostile token whose subject
    would not fit there answers 422 instead of a database error."""
    try:
        _StorableSubject(value=user.sub)
    except ValidationError as exc:
        raise RequestValidationError(exc.errors()) from exc
    return None
