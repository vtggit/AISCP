"""Supplier order minimums: the smallest order amount per supplier.

``GET /api/supplier-order-minimums`` lists every stored minimum as an
object with ``id``, ``source_system``, ``supplier_code``, ``amount``,
``updated_at`` (an ISO-8601 string) and ``updated_by``, ordered by
``source_system`` and then ``supplier_code`` — the route sorts the
rows itself in Unicode code point order, so the order does not depend
on the database's collation — and is open to any signed-in user.
``PUT /api/supplier-order-minimums`` stores a minimum — a new row, or
the amount of the row already stored for that
(``source_system``, ``supplier_code``) pair, keeping its ``id`` and
setting ``updated_at`` to now and ``updated_by`` to the caller's
username — and ``DELETE /api/supplier-order-minimums/{id}`` removes
one; both need the admin role, a signed-in non-admin getting 403
exactly as the purchase-budgets routes refuse.  Nothing checks that
the supplier exists: a minimum can be stored before the supplier is
synced.

A body whose text keys are not 1-to-64 (``source_system``) or
1-to-255 (``supplier_code``) characters, that have white space at
their start or end, a control character (code points 0 to 31 and 127
to 159) or a character that cannot be stored as UTF-8 (a lone
surrogate), a number/boolean/null in their place or a missing key,
and an ``amount`` that is not a finite number above 0 and at most
1,000,000,000,000 — including a string, a boolean, a null, a missing
amount and a raw body whose amount is ``Infinity``, ``NaN`` or a
number that overflows such as 1e400 — answer 422 before anything is
written, and a DELETE whose ``id`` is not an integer from 1 to
9223372036854775807 answers 422 too; every one of these answers in
FastAPI's HTTPValidationError envelope, never a 500.  For the
admin-only routes the 401/403 from authentication and authorization
come first.
"""

import json
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError

from app.api.erp_mappings import (
    require_admin,
    require_any_authenticated_user,
)
from app.auth.models import AuthUser
from app.models.supplier_order_minimums import (
    SupplierOrderMinimumIn,
    SupplierOrderMinimumOut,
)
from app.repositories.supplier_order_minimums_postgres_repository import (
    SupplierOrderMinimumPostgresRepository,
)

router = APIRouter(
    prefix="/api/supplier-order-minimums",
    tags=["supplier-order-minimums"],
)

_repo = SupplierOrderMinimumPostgresRepository()

# The largest integer the bigint id column holds.
BIGINT_MAX = 9_223_372_036_854_775_807


def _find_unstoreable(value):
    """The first value a 422 envelope could not echo back, or None.

    FastAPI's HTTPValidationError envelope echoes each rejected value
    back as the error's ``input`` — a missing-field error echoes the
    whole body, keys included — and the response serializer renders
    it with ``ensure_ascii=False`` and ``allow_nan=False`` before
    UTF-8 encoding.  Two kinds of hostile value break that rendering
    and would turn the 422 into a 500: a non-finite float (a JSON
    ``Infinity``/``NaN`` token, or a number so large it overflows to
    one on parse, such as 1e400) and a string holding a lone
    surrogate (the JSON string ``"\\ud800"``), which cannot be
    encoded as UTF-8 at all — as a body value or as a JSON key.  The
    body model's own 422 would echo such a value, so this guard must
    refuse it first; this walks the parsed body, keys included, and
    returns ``(loc, message)`` for the first such value.
    """

    def _loc_part(key: object) -> object:
        if isinstance(key, str):
            try:
                key.encode("utf-8")
            except UnicodeEncodeError:
                # A lone surrogate in a key would ride along in the
                # error's loc and break the envelope's own rendering.
                return key.encode("unicode_escape").decode("ascii")
        return key

    def walk(node, loc):
        if isinstance(node, dict):
            for key, child in node.items():
                if isinstance(key, str):
                    try:
                        key.encode("utf-8")
                    except UnicodeEncodeError:
                        return (
                            loc + [_loc_part(key)],
                            "must be a UTF-8 storeable string",
                        )
                found = walk(child, loc + [_loc_part(key)])
                if found is not None:
                    return found
            return None
        if isinstance(node, list):
            for index, child in enumerate(node):
                found = walk(child, loc + [index])
                if found is not None:
                    return found
            return None
        if isinstance(node, float) and not math.isfinite(node):
            return loc, "must be a finite number"
        if isinstance(node, str):
            try:
                node.encode("utf-8")
            except UnicodeEncodeError:
                return loc, "must be a UTF-8 storeable string"
        return None

    return walk(value, ["body"])


async def _reject_unstoreable_body(request: Request) -> None:
    """Refuse a body the 422 envelope cannot echo back, with a 422.

    A JSON ``Infinity``/``NaN`` token, a number that overflows to one
    on parse (1e400) or a string holding a lone surrogate — a body
    value or a JSON key — would pass the body model's own validation
    errors straight into the response, where the serializer cannot
    write them (a missing-field error echoes the whole body, keys
    included) — the rejection would become a 500.  This guard (the
    one the purchase-budgets route uses for the non-finite numbers,
    widened to the lone surrogates in values and keys) runs before
    the body model's validation: it reads the (cached) raw body and
    refuses any such value with a plain 422 in FastAPI's
    HTTPValidationError envelope first; a body without one passes
    through to the body model's own validation.
    """
    raw = await request.body()
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except ValueError:
        return
    found = _find_unstoreable(payload)
    if found is None:
        return
    loc, message = found
    raise RequestValidationError(
        [
            {
                "type": "value_error",
                "loc": loc,
                "msg": message,
                "input": "<unstoreable value>",
            }
        ]
    ) from None


@router.get("", response_model=list[SupplierOrderMinimumOut])
def list_minimums(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> list[dict]:
    """Every stored minimum, by source_system then supplier_code.

    Open to any signed-in user (the admin role is only needed for the
    mutations); the router's include-time gate answers 401 without a
    valid Bearer token.  The rows are sorted here, in Unicode code
    point order on (source_system, supplier_code), so the served order
    never depends on the database's collation.
    """
    rows = _repo.list()
    rows.sort(key=lambda row: (row["source_system"], row["supplier_code"]))
    return rows


@router.put("", response_model=SupplierOrderMinimumOut)
def upsert_minimum(
    body: SupplierOrderMinimumIn,
    current_user: AuthUser = Depends(require_admin),
    _storeable_body: None = Depends(_reject_unstoreable_body),
) -> dict:
    """Store one supplier's minimum; needs the admin role (403 otherwise).

    A new (source_system, supplier_code) pair inserts a row; a stored
    pair replaces its amount and bumps ``updated_at`` (now) and
    ``updated_by`` (the caller's username) — the id is kept.  The text
    keys and the finite, positive ``amount`` (at most
    1,000,000,000,000) must validate or the body answers 422 before
    anything is written.  Returns the stored row.
    """
    return _repo.upsert(
        source_system=body.source_system,
        supplier_code=body.supplier_code,
        amount=body.amount,
        updated_by=current_user.username or "",
    )


@router.delete("/{id}", status_code=204)
def delete_minimum(
    id: Annotated[int, Path(ge=1, le=BIGINT_MAX)],  # noqa: A002 - the path is /{id}
    current_user: AuthUser = Depends(require_admin),
) -> None:
    """Remove one minimum by id; needs the admin role (403 otherwise).

    ``id`` is an integer from 1 to 9223372036854775807, the largest
    the bigint column holds; anything else answers 422.  204 with no
    body when a row was removed, 404 when no row has that id.
    """
    if not _repo.delete(id):
        raise HTTPException(
            status_code=404,
            detail=f"Unknown supplier order minimum: {id}",
        )
    return None
