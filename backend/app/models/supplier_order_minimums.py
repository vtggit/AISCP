"""Pydantic models for supplier order minimums.

A supplier order minimum request is a ``source_system``, a
``supplier_code`` and an ``amount``: the body of
``PUT /api/supplier-order-minimums``.  Both text keys must be a
string of 1 to 64 (``source_system``) or 1 to 255 (``supplier_code``)
characters with no white space at the start or end, no control
character (code points 0 to 31 and 127 to 159) and no character that
cannot be stored as UTF-8 (a lone surrogate such as the JSON string
``"\ud800"``); a number, a boolean or a null in their place, a missing
key and an over-long value are model validation errors too.
``amount`` must be a finite number above 0 and at most ``AMOUNT_MAX``
(1,000,000,000,000) — a string, a boolean, a null or a missing amount
is refused as well, and a non-finite one (Infinity, NaN or an
overflow such as 1e400) is refused by the route's raw-body guard
before the model is even reached.  A violation is a model validation
error so a rejected body answers 422 in FastAPI's HTTPValidationError
envelope before anything is written.  The stored text is exactly the
string that was sent: nothing is trimmed or normalized.  Keys the
model does not name are ignored.
"""

from pydantic import BaseModel, Field, ValidationInfo, field_validator

AMOUNT_MAX = 1_000_000_000_000

_SOURCE_SYSTEM_MAX = 64
_SUPPLIER_CODE_MAX = 255


def _check_key(field: str, value: str) -> str:
    """Reject an unstoreable text key (a 422 on the API).

    The length bounds are Field constraints (min/max length); this
    check covers what a stored key can never hold: white space at the
    start or end, a control character (code points 0 to 31 and 127 to
    159, from NUL to the C1 controls) and a character that cannot be
    stored as UTF-8 — a lone surrogate such as the JSON string
    ``"\ud800"`` fails ``encode("utf-8")``.
    """
    if value != value.strip():
        raise ValueError(f"{field} must not start or end with white space")
    for char in value:
        code = ord(char)
        if code <= 31 or 127 <= code <= 159:
            raise ValueError(f"{field} must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be a UTF-8 storeable string") from None
    return value


class SupplierOrderMinimumIn(BaseModel):
    """The body of PUT /api/supplier-order-minimums.

    ``source_system`` is a 1-to-64-character string, ``supplier_code``
    a 1-to-255-character string — neither with edge white space, a
    control character or a lone surrogate — and ``amount`` a finite
    number above 0 and at most ``AMOUNT_MAX`` inclusive.  Anything
    else is rejected with a 422 before anything is written.
    """

    source_system: str = Field(min_length=1, max_length=_SOURCE_SYSTEM_MAX)
    supplier_code: str = Field(min_length=1, max_length=_SUPPLIER_CODE_MAX)
    amount: float = Field(gt=0, le=AMOUNT_MAX)

    @field_validator("source_system", "supplier_code")
    @classmethod
    def _check_keys(cls, value: str, info: ValidationInfo) -> str:
        return _check_key(info.field_name, value)

    @field_validator("amount", mode="before")
    @classmethod
    def _reject_non_numbers(cls, value: object) -> object:
        """A string or a boolean is not a number: a 422, not a coercion.

        Pydantic's lax float would coerce a numeric string (or a
        boolean) instead of refusing it, so the non-numeric kinds are
        rejected here, before the type conversion.
        """
        if isinstance(value, (str, bool)):
            raise ValueError("amount must be a number")
        return value


class SupplierOrderMinimumOut(BaseModel):
    """One stored supplier order minimum as served by the routes."""

    id: int
    source_system: str
    supplier_code: str
    amount: float
    updated_at: str
    updated_by: str
