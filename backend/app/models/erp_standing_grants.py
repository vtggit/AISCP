"""Pydantic models for ERP standing grants.

A standing grant request is a ``process``, a ``max_document_value``,
a ``max_daily_value`` and a ``max_data_age_hours``: the body of
``POST /api/standing-grants``.  ``process`` must be one of the two
release process names — ``order_plan_release`` or
``transfer_release`` — and anything else (another name, the empty
string, a number or a null) is refused.  Both values must be a finite
number above 0 and at most ``VALUE_MAX`` (1,000,000,000,000) — a
string, a boolean, a null or a missing value is refused as well, and a
non-finite one (Infinity, NaN or an overflow such as 1e400) is
refused by the route's raw-body guard before the model is even
reached.  ``max_daily_value`` must not be below ``max_document_value``
(the grant covers at least as much per day as in one document), and
``max_data_age_hours`` must be a whole number from 1 to 168 — a
boolean, a fractional number such as 2.5, a string or a null is
refused, as is any field the body does not define.  A violation is a
model validation error so a rejected body answers 422 in FastAPI's
HTTPValidationError envelope before anything is written.

The runs served by ``GET /api/standing-grants/runs`` are shaped by
``ErpStandingGrantRunOut``: one stored run with ``id``, ``process``
and ``grant_id``, ``started_at`` the ISO-8601 string with its UTC
offset, ``outcome`` and ``reason`` (null when the run has none),
``documents`` and ``value`` the count and the summed value of the
groups sent and ``left_out`` the groups the limits left out — each
either an ``ErpStandingGrantRunLeftOut`` with ``site_code``,
``supplier_code``, ``value`` (the number the entry's stored decimal
string reads as) and ``reason`` (an order-plan run's entry) or an
``ErpStandingGrantTransferRunLeftOut`` with ``from_site``,
``to_site``, ``value`` and ``reason`` (a transfer run's entry) — in
their stored order.
"""

import math
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

# The two processes a standing grant may cover.
PROCESS = Literal["order_plan_release", "transfer_release"]

# The largest value (in the ERP's reporting currency) a grant may cover.
VALUE_MAX = 1_000_000_000_000


class ErpStandingGrantIn(BaseModel):
    """The body of POST /api/standing-grants.

    ``process`` is one of the two release process names, both values
    a finite number above 0 and at most ``VALUE_MAX`` inclusive with
    ``max_daily_value`` not below ``max_document_value``, and
    ``max_data_age_hours`` a whole number from 1 to 168.  A field the
    model does not name is rejected as well.  Anything else is
    rejected with a 422 before anything is written.
    """

    model_config = ConfigDict(extra="forbid")

    process: PROCESS
    max_document_value: float = Field(gt=0, le=VALUE_MAX)
    max_daily_value: float = Field(gt=0, le=VALUE_MAX)
    max_data_age_hours: int = Field(ge=1, le=168)

    @field_validator("max_document_value", "max_daily_value", mode="before")
    @classmethod
    def _reject_non_numbers(cls, value: object, info: ValidationInfo) -> object:
        """A string or a boolean is not a number: a 422, not a coercion.

        Pydantic's lax float would coerce a numeric string (or a
        boolean) instead of refusing it, so the non-numeric kinds are
        rejected here, before the type conversion.  A non-finite float
        (a JSON ``Infinity``/``NaN`` token or an overflow such as
        1e400) is refused here too, in case the route's raw-body guard
        is not in the picture.
        """
        if isinstance(value, (str, bool)):
            raise ValueError(f"{info.field_name} must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{info.field_name} must be a finite number")
        return value

    @field_validator("max_data_age_hours", mode="before")
    @classmethod
    def _reject_non_whole_numbers(cls, value: object, info: ValidationInfo) -> object:
        """A string or a boolean is not a whole number: a 422, not a
        coercion.  A fractional number such as 2.5 is refused by the
        int conversion; 168.0, a whole number, passes it."""
        if isinstance(value, (str, bool)):
            raise ValueError(f"{info.field_name} must be a whole number")
        return value

    @model_validator(mode="after")
    def _daily_covers_document(self) -> "ErpStandingGrantIn":
        """The daily ceiling must not sit below the per-document one."""
        if self.max_daily_value < self.max_document_value:
            raise ValueError("max_daily_value must not be below max_document_value")
        return self


class ErpStandingGrantOut(BaseModel):
    """One stored standing grant as served by the routes."""

    id: int
    process: str
    max_document_value: float
    max_daily_value: float
    max_data_age_hours: int
    created_at: str
    created_by: str
    revoked_at: str | None
    revoked_by: str | None
    in_force: bool


class ErpStandingGrantRunLeftOut(BaseModel):
    """One group a run's limits left out, as the run stored it.

    ``site_code`` and ``supplier_code`` name the group, ``value`` the
    number the entry's stored decimal string reads as and ``reason``
    the limit that left the group out (``document_limit`` or
    ``daily_limit``).
    """

    site_code: str
    supplier_code: str
    value: float
    reason: str


class ErpStandingGrantTransferRunLeftOut(BaseModel):
    """One transfer group a run's limits left out, as the run stored
    it.

    ``from_site`` and ``to_site`` name the group's pair, ``value``
    the number the entry's stored decimal string reads as (the
    summed value of the suggestions that have one for a
    ``no_value`` entry) and ``reason`` the limit that left the group
    out (``no_value``, ``document_limit`` or ``daily_limit``).
    """

    from_site: str
    to_site: str
    value: float
    reason: str


class ErpStandingGrantRunOut(BaseModel):
    """One stored standing-grant run as served by
    ``GET /api/standing-grants/runs``.

    ``process`` and ``grant_id`` the run's process and the grant in
    force it ran under, ``started_at`` the ISO-8601 string with its
    UTC offset, ``outcome`` the run's outcome, ``reason`` why the run
    did not release (null when the run has none), ``documents`` the
    count and ``value`` the summed value of the groups it sent and
    ``left_out`` the groups its limits left out, in their stored
    order.
    """

    id: int
    process: str
    grant_id: int
    started_at: str
    outcome: str
    reason: str | None
    documents: int
    value: float
    left_out: list[ErpStandingGrantRunLeftOut | ErpStandingGrantTransferRunLeftOut]
