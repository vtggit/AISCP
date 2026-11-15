"""Guidance: the publication of AIERP's whole guidance.

``GET /api/guidance`` reads the current guidance — the newest
publication of ``guidance_publications`` (or null when nothing was
ever published) and the versions whose ``last_publication_id`` names
it, each with a ``stamp`` (the key, ``@`` and the hash) and a
``since`` (the ``published_at`` of the publication the row's
``first_publication_id`` names: the moment AIERP first held this
version) — for any signed-in user, admin or not: without a valid
token it answers 401, every signed-in user gets the same answer, and
it writes nothing.  The route sorts the versions itself — by type in
the order direction, boundary, roadmap, design; within a type by the
number at the end of the key, compared as a number and ascending; and
for the same number by the key in Unicode code point order — so the
order never depends on the database's collation, and the fixed field
set is serialized with explicit null values and no extra field.

``PUT /api/guidance`` publishes the whole guidance — a ``source``
(plain text of 1 to 120 characters: what was published, a commit or a
label) and ``items`` (a list of 1 to 2000 items, each with a ``key``,
a ``type`` the key's letter names, a ``scope`` the key names, an
``area``, an ``area_title``, a ``title``, a ``statement``, a
``status``, a ``decided_by``, a real ``decided_on`` date and a
``hash`` worked out from the item's own words, plus three optional
fields) — and needs the admin role, a signed-in non-admin getting 403
exactly as the purchase-budgets routes refuse.  The publication
stores one row in ``guidance_publications`` and, for each item, bumps
the matching current version's ``last_publication_id`` or inserts a
new version row; a current key missing from the body stops being
current without its row being touched.  All of this is one database
transaction: when any write fails, nothing is stored.

A body that breaks any of those rules, in any item (one wrong item
refuses the whole publication), a body that is not an object, a
missing required key, a number, boolean, null or list where a string
is asked for, and a raw body that holds a non-finite number anywhere
(``Infinity``, ``NaN``, or one that overflows such as 1e400) answer
422 before anything is written, every one of them in FastAPI's
HTTPValidationError envelope, never a 500.  For the admin-only route
the 401/403 from authentication and authorization come first.  A key
the body or an item does not name is ignored.

The checks and the storing run the shared code of
``app.guidance_publish`` (the deployment's command publishes the same
body the same way, with ``published_by`` the string ``deployment``),
so the rules are written once: the raw-body storeability walk and the
body model's validation are the command's checks, and the repository's
one transaction is the command's storing.  This module keeps what is
the route's own: the admin gate, the guard that raises the 422 and
the answer model.

``GET /api/guidance/items/{key}`` answers the history of one item —
``key``, a ``current`` flag (true when one of its versions is
current, its ``last_publication_id`` the id of the newest
publication) and ``versions``: every ``guidance_versions`` row with
that key, the newest first (by row ``id``, descending), each with
its fixed field set, its own ``current`` flag and its ``first`` and
``last`` publication (the publications the row's
``first_publication_id`` and ``last_publication_id`` name:
``publication_id``, ``source``, ``published_at`` as an ISO-8601
string with a UTC offset and ``published_by``) — for any signed-in
user, admin or not: without a valid token it answers 401, a key that
does not match ``^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}$`` answers
422 in FastAPI's HTTPValidationError envelope and a key no row has
answers 404.  It writes nothing.

``GET /api/guidance/stamps/{stamp}`` answers what one stamp names —
a stamp is a key, the character ``@`` and 10 characters from ``0``
to ``9`` and ``a`` to ``f``: anything else answers 422 in
FastAPI's HTTPValidationError envelope, and a stamp no row has
answers 404, also when the key exists with other hashes — with
``stamp``, ``key`` and ``hash``, the ``type``, ``title`` and
``statement`` those rows carry (the words the stamp names),
``current`` (true when a row with that key and hash is current),
``superseded`` (true when the key has a current row with another
hash) and ``versions``: every row with that key and that hash, the
newest first.  Like the item's history it is open to any signed-in
user, admin or not, and writes nothing.
"""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Request
from fastapi.exceptions import RequestValidationError

from app.api.erp_mappings import require_admin
from app.auth.dependencies import require_authenticated_user
from app.auth.models import AuthUser
from app.guidance_publish import find_unstoreable, publish
from app.models.guidance import (
    KEY_PATTERN,
    GuidanceGetOut,
    GuidanceIn,
    GuidanceItemHistoryOut,
    GuidanceOut,
    GuidanceStampOut,
)
from app.repositories.guidance_postgres_repository import (
    GuidancePostgresRepository,
)

router = APIRouter(prefix="/api/guidance", tags=["guidance"])

# The stamp the history routes name: the key, the character ``@`` and
# the 10 lowercase hex digits the hash rule works out — the key and
# the hash of one version, in that order.
STAMP_PATTERN = r"^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}@[0-9a-f]{10}$"


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
    runs the shared walk ``find_unstoreable`` (the one the deployment
    command's checks run on the same rules) and refuses any such
    value with a plain 422 in FastAPI's HTTPValidationError envelope
    first; a body without one passes through to the body model's own
    validation.
    """
    raw = await request.body()
    if not raw:
        return
    try:
        payload = json.loads(raw)
    except ValueError:
        return
    found = find_unstoreable(payload)
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


# The type order the GET route answers in: direction, boundary,
# roadmap, design.
_TYPE_ORDER = {
    "direction": 0,
    "boundary": 1,
    "roadmap": 2,
    "design": 3,
}

_repo = GuidancePostgresRepository()


def require_signed_in(
    current_user: AuthUser = Depends(require_authenticated_user),
) -> AuthUser:
    """The GET route's own authentication dependency.

    The route is open to any signed-in user — admin or not, the
    answer is the same for both.  The user is taken from
    ``require_authenticated_user`` (401 without a valid Bearer
    token, so this dependency answers 401 even when the router is
    mounted without the app's include-time gate) and is passed
    through unchanged.
    """
    return current_user


def _utc(value: datetime) -> datetime:
    """The row's timestamp expressed in UTC, aware.

    The ``timestamptz`` columns come back aware; a naive value
    (which the column type cannot produce) is taken as UTC, so the
    answer always carries a UTC offset.
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _order_key(row: dict) -> tuple[int, int, str]:
    """The route's own order for one version row.

    The type in the order direction, boundary, roadmap, design;
    within a type the number at the end of the key, compared as a
    number and ascending (``SHOP-D-2`` before ``SHOP-D-10``); and
    for the same number the key in Unicode code point order
    (``ACME-D-2`` before ``SHOP-D-2``).
    """
    return (
        _TYPE_ORDER[row["type"]],
        int(row["key"].rsplit("-", 1)[1]),
        row["key"],
    )


def _version_ref(row: dict, prefix: str) -> dict:
    """The publication the row's first/last publication id names.

    ``prefix`` is ``first`` or ``last``: the joined columns of the
    publication the row's ``first_publication_id`` or
    ``last_publication_id`` names.
    """
    return {
        "publication_id": row[f"{prefix}_publication_id"],
        "source": row[f"{prefix}_source"],
        "published_at": _utc(row[f"{prefix}_published_at"]),
        "published_by": row[f"{prefix}_published_by"],
    }


def _version(row: dict, newest_id: int | None) -> dict:
    """One version row as the two history routes serve it.

    The fixed field set with explicit null values and no extra field:
    ``hash``, ``stamp`` (the key, ``@`` and the hash), ``type``,
    ``scope``, ``area``, ``area_title``, ``title``, ``statement``,
    ``status``, ``decided_by``, ``decided_on`` (a ``YYYY-MM-DD``
    string), ``decided_under``, ``decided_ref`` and ``authority``
    (each a string or null), ``current`` (true when the row's
    ``last_publication_id`` is the id of the newest publication) and
    ``first``/``last`` (the publications the row's
    ``first_publication_id`` and ``last_publication_id`` name).
    """
    return {
        "hash": row["hash"],
        "stamp": f"{row['key']}@{row['hash']}",
        "type": row["type"],
        "scope": row["scope"],
        "area": row["area"],
        "area_title": row["area_title"],
        "title": row["title"],
        "statement": row["statement"],
        "status": row["status"],
        "decided_by": row["decided_by"],
        "decided_on": row["decided_on"].isoformat(),
        "decided_under": row["decided_under"],
        "decided_ref": row["decided_ref"],
        "authority": row["authority"],
        "current": row["last_publication_id"] == newest_id,
        "first": _version_ref(row, "first"),
        "last": _version_ref(row, "last"),
    }


@router.get("", response_model=GuidanceGetOut)
def read_guidance(
    current_user: AuthUser = Depends(require_signed_in),
) -> dict:
    """The current guidance, for any signed-in user (401 without a token).

    Read-only: the route writes nothing.  ``publication`` is null
    and ``items`` empty when nothing was ever published; otherwise
    ``publication`` is the newest row of ``guidance_publications``
    (``id``, ``source``, ``published_at`` as an ISO-8601 string with
    a UTC offset, ``published_by`` and ``item_count``) and
    ``items`` the versions whose ``last_publication_id`` is that
    id — each with ``key``, ``hash``, ``stamp`` (the key, ``@`` and
    the hash), ``type``, ``scope``, ``area``, ``area_title``,
    ``title``, ``statement``, ``status``, ``decided_by``,
    ``decided_on`` (a ``YYYY-MM-DD`` string), ``decided_under``,
    ``decided_ref`` and ``authority`` (each a string or null) and
    ``since`` (the ``published_at`` of the publication the row's
    ``first_publication_id`` names: the moment AIERP first held this
    version).  The route sorts the versions itself: by type in the
    order direction, boundary, roadmap, design; within a type by the
    number at the end of the key, compared as a number and
    ascending; and for the same number by the key in Unicode code
    point order — so the order never depends on the database's
    collation.  The fixed field set is serialized with explicit
    null values and no extra field.
    """
    data = _repo.current()
    publication = data["publication"]
    if publication is None:
        return {"publication": None, "items": []}
    return {
        "publication": {
            "id": publication["id"],
            "source": publication["source"],
            "published_at": _utc(publication["published_at"]),
            "published_by": publication["published_by"],
            "item_count": publication["item_count"],
        },
        "items": [
            {
                "key": row["key"],
                "hash": row["hash"],
                "stamp": f"{row['key']}@{row['hash']}",
                "type": row["type"],
                "scope": row["scope"],
                "area": row["area"],
                "area_title": row["area_title"],
                "title": row["title"],
                "statement": row["statement"],
                "status": row["status"],
                "decided_by": row["decided_by"],
                "decided_on": row["decided_on"].isoformat(),
                "decided_under": row["decided_under"],
                "decided_ref": row["decided_ref"],
                "authority": row["authority"],
                "since": _utc(row["since"]),
            }
            for row in sorted(data["items"], key=_order_key)
        ],
    }


@router.put("", response_model=GuidanceOut)
def publish_guidance(
    current_user: AuthUser = Depends(require_admin),
    _storeable_body: None = Depends(_reject_unstoreable_body),
    body: GuidanceIn = Body(),
) -> dict:
    """Publish the whole guidance; needs the admin role (403 otherwise).

    The admin check (401 without a valid token, 403 for a signed-in
    non-admin) and the raw-body storeability guard are declared
    before the body on purpose: both run before the body model is
    validated, so the 401/403 come before any validation and a raw
    body the 422 envelope could not echo back is refused by the
    guard first.

    ``source`` and every item must validate or the body answers 422
    before anything is written (one wrong item refuses the whole
    publication).  When every item matches the current version of its
    key and every current key is in the body, nothing is written;
    otherwise one new ``guidance_publications`` row (``source`` as
    sent, ``published_by`` the caller's username, ``item_count`` the
    number of items) and, per item, a bump of the matching current
    row's ``last_publication_id`` or a new ``guidance_versions`` row —
    all in one database transaction.  Returns ``changed``,
    ``publication_id`` (the id of the newest publication after the
    call), ``items``, ``new_versions`` and ``withdrawn``.
    """
    return publish(body, published_by=current_user.username or "")


@router.get("/items/{key}", response_model=GuidanceItemHistoryOut)
def read_item_history(
    key: Annotated[str, Path(pattern=KEY_PATTERN)],
    current_user: AuthUser = Depends(require_signed_in),
) -> dict:
    """The history of one item, for any signed-in user (401 without a token).

    Read-only: the route writes nothing.  The key in the path must
    match ``^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}$`` or the request
    answers 422 in FastAPI's HTTPValidationError envelope; a key no
    row of ``guidance_versions`` has answers 404.  Otherwise the
    answer is ``key``, ``current`` (true when one of the item's
    versions is current — its ``last_publication_id`` is the id of
    the newest publication) and ``versions``: every row with that
    key, the newest first (by row ``id``, descending), each with its
    fixed field set, its ``current`` flag and its ``first``/``last``
    publication (``publication_id``, ``source``, ``published_at`` as
    an ISO-8601 string with a UTC offset and ``published_by``).
    Every signed-in user, admin or not, gets the same answer.
    """
    data = _repo.item_history(key)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Unknown guidance item: {key}")
    newest_id = data["newest_publication_id"]
    versions = [_version(row, newest_id) for row in data["versions"]]
    return {
        "key": key,
        "current": any(version["current"] for version in versions),
        "versions": versions,
    }


@router.get("/stamps/{stamp}", response_model=GuidanceStampOut)
def read_stamp(
    stamp: Annotated[str, Path(pattern=STAMP_PATTERN)],
    current_user: AuthUser = Depends(require_signed_in),
) -> dict:
    """What one stamp names, for any signed-in user (401 without a token).

    Read-only: the route writes nothing.  The stamp in the path is a
    key, the character ``@`` and 10 characters from ``0`` to ``9``
    and ``a`` to ``f``; anything else answers 422 in FastAPI's
    HTTPValidationError envelope.  A stamp no row has answers 404 —
    also when the key exists with other hashes.  Otherwise the answer
    is ``stamp``, ``key`` and ``hash`` (the stamp's parts), the
    ``type``, ``title`` and ``statement`` those rows carry (the words
    the stamp names), ``current`` (true when a row with that key and
    hash is current), ``superseded`` (true when the key has a current
    row with another hash) and ``versions``: every row with that key
    and that hash, the newest first (by row ``id``, descending).
    Every signed-in user, admin or not, gets the same answer.
    """
    key, _, item_hash = stamp.partition("@")
    data = _repo.item_history(key)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Unknown guidance stamp: {stamp}")
    rows = [row for row in data["versions"] if row["hash"] == item_hash]
    if not rows:
        raise HTTPException(status_code=404, detail=f"Unknown guidance stamp: {stamp}")
    newest_id = data["newest_publication_id"]
    newest = rows[0]  # the rows come back newest first
    return {
        "stamp": stamp,
        "key": key,
        "hash": item_hash,
        "type": newest["type"],
        "title": newest["title"],
        "statement": newest["statement"],
        "current": any(row["last_publication_id"] == newest_id for row in rows),
        "superseded": any(
            row["last_publication_id"] == newest_id and row["hash"] != item_hash
            for row in data["versions"]
        ),
        "versions": [_version(row, newest_id) for row in rows],
    }
