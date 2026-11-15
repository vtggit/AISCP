"""Pydantic models for per-month purchase budgets.

A purchase budget request is one ``month`` and one ``amount``: the
``YYYY-MM`` month rides in the URL path of
``PUT /api/purchase-budgets/{month}`` (validated against
``MONTH_PATTERN``) and the amount is the body's number.  ``amount``
must be a finite number between ``AMOUNT_MIN`` (0) and ``AMOUNT_MAX``
(1,000,000,000,000) inclusive; a violation is a model validation error
so a rejected month or amount answers 422 in FastAPI's
HTTPValidationError envelope before anything is written.  The
database's CHECK constraints mirror the month pattern and the
non-negativity at the table level.
"""

from pydantic import BaseModel, Field, field_validator

# YYYY-MM with a real month, 01 to 12.  Anchored on purpose: pydantic
# matches the pattern with re.search semantics, so the ^...$ anchors
# are what reject 2026-11x, 2026-121 and friends with a 422.  The
# digits are [0-9], mirroring the table's CHECK, on purpose: re's \d
# would also accept Unicode digits (2026 in Arabic-Indic digits),
# which the database would then refuse with a CHECK violation — a 500
# for a path the API layer should have answered 422 for.
MONTH_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])$"

AMOUNT_MIN = 0
AMOUNT_MAX = 1_000_000_000_000


class PurchaseBudgetIn(BaseModel):
    """The amount of PUT /api/purchase-budgets/{month}.

    ``amount`` is the month's budget: a finite number between
    ``AMOUNT_MIN`` and ``AMOUNT_MAX`` inclusive.  A negative or
    non-finite number (the ge/le constraints refuse NaN and the
    infinities), one above the cap, a string, a boolean, a null or a
    missing body is rejected with a 422 before anything is written.
    """

    amount: float = Field(ge=AMOUNT_MIN, le=AMOUNT_MAX)

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


class PurchaseBudgetOut(BaseModel):
    """One stored purchase budget as served by the purchase-budgets routes."""

    id: int
    month: str
    amount: float
    updated_at: str
    updated_by: str
