"""Pydantic models for cross-ERP code mappings.

A mapping binds one AIERP source code (an ``item``, ``site`` or
``vendor`` code) to the code of the same thing in one target system
(``odoo`` today), per (kind, target_system, source_code).  These models
shape the payload and the stored row; the business rules are model
validations so a rejected body answers 422 in FastAPI's
HTTPValidationError envelope: a kind outside item/site/vendor, a
target_system other than odoo, or a blank, NUL-containing or
over-255-character code.  The (kind, target_system, source_code)
uniqueness is the table's unique constraint, not a model rule.
"""

from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

_NUL = chr(0)
_MAX_CODE_LENGTH = 255


def _check_code(field: str, value: str) -> str:
    """Reject a blank or NUL-containing code (a 422 on the API).

    The 255-character bound is a Field constraint (max_length); this
    check covers the blank and NUL cases a stored code can never hold.
    """
    if value.strip() == "":
        raise ValueError(f"{field} must not be blank")
    if _NUL in value:
        raise ValueError(f"{field} must not contain NUL bytes")
    return value


class ErpMappingIn(BaseModel):
    """The body of PUT /api/erp-mappings.

    ``kind`` is one of item/site/vendor, ``target_system`` is odoo, and
    both codes are non-blank, NUL-free strings of at most 255
    characters.
    """

    kind: Literal["item", "site", "vendor"]
    target_system: Literal["odoo"]
    source_code: str = Field(min_length=1, max_length=_MAX_CODE_LENGTH)
    target_code: str = Field(min_length=1, max_length=_MAX_CODE_LENGTH)

    @model_validator(mode="after")
    def _check_codes(self) -> Self:
        _check_code("source_code", self.source_code)
        _check_code("target_code", self.target_code)
        return self


class ErpMappingOut(BaseModel):
    """One stored mapping as served by the erp-mappings routes."""

    id: int
    kind: str
    target_system: str
    source_code: str
    target_code: str
    created_at: str
    updated_at: str
