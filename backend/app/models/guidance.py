"""Pydantic models for the guidance publication.

The body of ``PUT /api/guidance`` is a ``source`` (plain text of 1 to
120 characters: what was published, a commit or a label) and ``items``
(a list of 1 to 2000 items, the whole guidance).  Every item has a
``key`` matching ``^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}$``, a
``type`` one of ``direction``, ``roadmap``, ``design``, ``boundary``
(the one the key's letter names: N direction, R roadmap, D design, B
boundary), a ``scope`` that is the part of the key before its first
hyphen, an ``area`` matching ``^[a-z][a-z0-9-]{1,48}$``, an
``area_title`` and a ``title`` (each plain text of 1 to 90
characters), a ``statement`` (plain text of 1 to 1200 characters), a
``status`` one of ``proposed``, ``active``, ``retired``, a
``decided_by`` (the string ``engine``, or a string matching
``^(github|model):[A-Za-z0-9][A-Za-z0-9._-]{0,63}$``), a
``decided_on`` (a ``YYYY-MM-DD`` string that is a real calendar date)
and a ``hash`` — a string of exactly 10 characters from ``0`` to ``9``
and ``a`` to ``f`` that equals the first 10 hexadecimal digits, in
lower case, of the SHA-256 of the UTF-8 bytes of
``type + "\n" + title + "\n" + statement + "\n"`` — plus three
optional fields, each of which may be absent or null: ``decided_under``
(plain text of 1 to 40 characters), ``decided_ref`` (plain text of 1 to
80 characters) and ``authority`` (a string matching
``^[a-z][a-z0-9-]{1,31}$``).  "Plain text of 1 to N characters" means a
string of 1 to N characters that equals itself with every run of white
space replaced by one space and both ends trimmed
(``value == " ".join(value.split())``), that holds no control character
(code points 0 to 31 and 127 to 159) and no character that cannot be
stored as UTF-8 (a lone surrogate such as the JSON string
``"\ud800"``).  No key appears twice in one body.

A number, boolean, null or list where a string is asked for, a missing
required key, a body that is not an object, a broken rule in any item
(one wrong item refuses the whole publication) and an over-long or
over-large list are model validation errors: the body answers 422 in
FastAPI's HTTPValidationError envelope before anything is written, and
a raw body holding a non-finite number anywhere is refused by the
route's raw-body guard before the model is even reached.  The stored
text is exactly the string that was sent: nothing is trimmed or
normalized.  Keys the models do not name are ignored.

The answer models of the two read-only history routes serve
the same rows: ``GuidanceItemHistoryOut`` (``GET
/api/guidance/items/{key}``) holds the key, a ``current``
flag and every version row of the key newest first, and
``GuidanceStampOut`` (``GET /api/guidance/stamps/{stamp}``)
holds the stamp's parts, the words the stamp names, a
``current`` and a ``superseded`` flag and the key's rows of
that hash newest first.  Each version row is served by
``GuidanceVersionOut`` with its ``first`` and ``last``
publication described by ``GuidancePublicationRefOut``.
"""

import hashlib
import re
from datetime import date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)

# Anchored on purpose: a pattern field is matched over the whole value,
# and the [0-9] digits mirror the table's rules on purpose — re's \d
# would also accept Unicode digits, which would then fail the hash
# rule or the database's CHECK.
KEY_PATTERN = r"^[A-Z][A-Z0-9]{1,7}-[NRDB]-[0-9]{1,4}$"
AREA_PATTERN = r"^[a-z][a-z0-9-]{1,48}$"
AUTHORITY_PATTERN = r"^[a-z][a-z0-9-]{1,31}$"
HASH_PATTERN = r"^[0-9a-f]{10}$"
DECIDED_BY_PATTERN = r"^(github|model):[A-Za-z0-9][A-Za-z0-9._-]{0,63}$"
DECIDED_ON_PATTERN = r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"

SOURCE_MAX = 120
AREA_TITLE_MAX = 90
TITLE_MAX = 90
STATEMENT_MAX = 1200
DECIDED_UNDER_MAX = 40
DECIDED_REF_MAX = 80
ITEMS_MIN = 1
ITEMS_MAX = 2000

# The type one key letter names: N direction, R roadmap, D design,
# B boundary.
TYPE_BY_LETTER = {
    "N": "direction",
    "R": "roadmap",
    "D": "design",
    "B": "boundary",
}


def _check_plain_text(field: str, value: str) -> str:
    """Reject a value that is not plain text of 1 to N characters (422).

    The length bounds are Field constraints (min/max length); this
    check covers the plain-text rule: the value must equal itself with
    every run of white space replaced by one space and both ends
    trimmed (no edge white space, no run of two or more spaces, no
    line break inside), hold no control character (code points 0 to 31
    and 127 to 159) and be storable as UTF-8 — a lone surrogate such as
    the JSON string ``"\\ud800"`` fails ``encode("utf-8")``.
    """
    if value != " ".join(value.split()):
        raise ValueError(
            f"{field} must be plain text (single spaces, no edge white space)"
        )
    for char in value:
        code = ord(char)
        if code <= 31 or 127 <= code <= 159:
            raise ValueError(f"{field} must not contain control characters")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"{field} must be a UTF-8 storeable string") from None
    return value


def item_hash(type: str, title: str, statement: str) -> str:
    """The hash AIERP works out from an item's own words.

    The first 10 hexadecimal digits, in lower case, of the SHA-256 of
    the UTF-8 bytes of ``type + "\n" + title + "\n" + statement +
    "\n"``.
    """
    words = f"{type}\n{title}\n{statement}\n"
    return hashlib.sha256(words.encode("utf-8")).hexdigest()[:10]


class GuidanceItemIn(BaseModel):
    """One item of the guidance body.

    Every field must validate or the whole publication is refused with
    a 422 before anything is written (one wrong item refuses the whole
    publication).  The stored text is exactly the string that was
    sent.
    """

    key: str = Field(pattern=KEY_PATTERN)
    type: Literal["direction", "roadmap", "design", "boundary"]
    scope: str
    area: str = Field(pattern=AREA_PATTERN)
    area_title: str = Field(min_length=1, max_length=AREA_TITLE_MAX)
    title: str = Field(min_length=1, max_length=TITLE_MAX)
    statement: str = Field(min_length=1, max_length=STATEMENT_MAX)
    status: Literal["proposed", "active", "retired"]
    decided_by: str
    decided_on: str
    hash: str = Field(pattern=HASH_PATTERN)
    decided_under: str | None = Field(
        default=None, min_length=1, max_length=DECIDED_UNDER_MAX
    )
    decided_ref: str | None = Field(
        default=None, min_length=1, max_length=DECIDED_REF_MAX
    )
    authority: str | None = Field(default=None, pattern=AUTHORITY_PATTERN)

    @field_validator("area_title", "title", "statement")
    @classmethod
    def _plain_text(cls, value: str, info: ValidationInfo) -> str:
        return _check_plain_text(info.field_name, value)

    @field_validator("decided_under", "decided_ref")
    @classmethod
    def _optional_plain_text(
        cls, value: str | None, info: ValidationInfo
    ) -> str | None:
        if value is None:
            return None
        return _check_plain_text(info.field_name, value)

    @field_validator("decided_by")
    @classmethod
    def _check_decided_by(cls, value: str) -> str:
        if value == "engine":
            return value
        if re.fullmatch(DECIDED_BY_PATTERN, value):
            return value
        raise ValueError("decided_by must be 'engine' or 'github:...' or 'model:...'")

    @field_validator("decided_on")
    @classmethod
    def _check_decided_on(cls, value: str) -> str:
        if not re.fullmatch(DECIDED_ON_PATTERN, value):
            raise ValueError("decided_on must be a YYYY-MM-DD string")
        try:
            date.fromisoformat(value)
        except ValueError:
            raise ValueError("decided_on must be a real calendar date") from None
        return value

    @model_validator(mode="after")
    def _check_item_consistency(self) -> "GuidanceItemIn":
        """The key names the scope, the type and the hash's words.

        The scope must be the part of the key before its first hyphen,
        the type must be the one the key's letter names, and the hash
        must be the one worked out from the item's own words (type,
        title and statement).
        """
        parts = self.key.split("-")
        if self.scope != parts[0]:
            raise ValueError(
                "scope must be the part of the key before its first hyphen"
            )
        expected_type = TYPE_BY_LETTER[parts[1]]
        if self.type != expected_type:
            raise ValueError(f"type must be {expected_type!r} for this key")
        expected_hash = item_hash(self.type, self.title, self.statement)
        if self.hash != expected_hash:
            raise ValueError(f"hash must be {expected_hash!r} for the item's own words")
        return self


class GuidanceIn(BaseModel):
    """The body of PUT /api/guidance: the whole guidance.

    ``source`` is plain text of 1 to 120 characters (what was
    published, a commit or a label) and ``items`` a list of 1 to 2000
    items; no key appears twice in one body.  Anything else is
    rejected with a 422 before anything is written.
    """

    source: str = Field(min_length=1, max_length=SOURCE_MAX)
    items: list[GuidanceItemIn] = Field(min_length=ITEMS_MIN, max_length=ITEMS_MAX)

    @field_validator("source")
    @classmethod
    def _check_source(cls, value: str) -> str:
        return _check_plain_text("source", value)

    @model_validator(mode="after")
    def _check_unique_keys(self) -> "GuidanceIn":
        keys = [item.key for item in self.items]
        if len(keys) != len(set(keys)):
            raise ValueError("a key appears twice in one body")
        return self


class GuidanceOut(BaseModel):
    """The answer of PUT /api/guidance.

    ``changed`` is false when nothing was written; ``publication_id``
    is the id of the newest publication after the call; ``items`` the
    number of items in the body; ``new_versions`` the version rows
    inserted; ``withdrawn`` the keys that were current before the call
    and are not in the body.
    """

    changed: bool
    publication_id: int
    items: int
    new_versions: int
    withdrawn: int


class GuidancePublicationOut(BaseModel):
    """The newest publication, as served by GET /api/guidance.

    ``id`` is the row's id (the highest, the newest), ``source`` the
    text as stored, ``published_at`` the moment the row was written
    (an ISO-8601 string with a UTC offset), ``published_by`` the
    publisher's username (or ``deployment``) and ``item_count`` the
    number of items the publication held.
    """

    model_config = ConfigDict(extra="forbid")

    id: int
    source: str
    published_at: datetime
    published_by: str
    item_count: int


class GuidanceItemOut(BaseModel):
    """One current version, as served by GET /api/guidance.

    ``stamp`` is the key, the character ``@`` and the hash — the
    stamp that names this exact version.  ``decided_under``,
    ``decided_ref`` and ``authority`` are null when the version was
    published without them.  ``since`` is the ``published_at`` of the
    publication the row's ``first_publication_id`` names (an
    ISO-8601 string with a UTC offset): the moment AIERP first held
    this version.
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    hash: str
    stamp: str
    type: str
    scope: str
    area: str
    area_title: str
    title: str
    statement: str
    status: str
    decided_by: str
    decided_on: str
    decided_under: str | None
    decided_ref: str | None
    authority: str | None
    since: datetime


class GuidanceGetOut(BaseModel):
    """The answer of GET /api/guidance.

    Exactly two keys: ``publication`` (null when nothing was ever
    published) and ``items`` (the current versions — the
    ``guidance_versions`` rows whose ``last_publication_id`` is the
    publication's id; empty when nothing was ever published) — in
    the route's own order, which the route sorts: by type in the
    order direction, boundary, roadmap, design; within a type by the
    number at the end of the key, compared as a number and
    ascending; and for the same number by the key in Unicode code
    point order.  The fixed field set is serialized with explicit
    null values; no extra field is ever emitted.
    """

    model_config = ConfigDict(extra="forbid")

    publication: GuidancePublicationOut | None
    items: list[GuidanceItemOut]


class GuidancePublicationRefOut(BaseModel):
    """The publication one version row's end names.

    ``publication_id`` is the row's id in ``guidance_publications``,
    ``source`` the text as stored, ``published_at`` the moment the row
    was written (an ISO-8601 string with a UTC offset) and
    ``published_by`` the publisher's username (or ``deployment``).
    """

    model_config = ConfigDict(extra="forbid")

    publication_id: int
    source: str
    published_at: datetime
    published_by: str


class GuidanceVersionOut(BaseModel):
    """One version row, as the two history routes serve it.

    ``stamp`` is the key, the character ``@`` and the hash — the
    stamp that names this exact version.  ``decided_on`` is the
    row's date as a ``YYYY-MM-DD`` string; ``decided_under``,
    ``decided_ref`` and ``authority`` are null when the version was
    published without them.  ``current`` is true when the row's
    ``last_publication_id`` is the id of the newest publication.
    ``first`` and ``last`` describe the publications the row's
    ``first_publication_id`` and ``last_publication_id`` name.
    """

    model_config = ConfigDict(extra="forbid")

    hash: str
    stamp: str
    type: str
    scope: str
    area: str
    area_title: str
    title: str
    statement: str
    status: str
    decided_by: str
    decided_on: str
    decided_under: str | None
    decided_ref: str | None
    authority: str | None
    current: bool
    first: GuidancePublicationRefOut
    last: GuidancePublicationRefOut


class GuidanceItemHistoryOut(BaseModel):
    """The answer of GET /api/guidance/items/{key}.

    ``key`` is the key asked for, ``current`` true when one of the
    item's versions is current (its ``last_publication_id`` is the id
    of the newest publication) and ``versions`` every
    ``guidance_versions`` row with that key, the newest first (by row
    ``id``, descending).
    """

    model_config = ConfigDict(extra="forbid")

    key: str
    current: bool
    versions: list[GuidanceVersionOut]


class GuidanceStampOut(BaseModel):
    """The answer of GET /api/guidance/stamps/{stamp}.

    ``stamp`` is the key, the character ``@`` and the hash, ``key``
    and ``hash`` its parts and ``type``, ``title`` and
    ``statement`` the words the stamp names.  ``current`` is true
    when a row with that key and hash is current, ``superseded`` true
    when the key has a current row with another hash, and
    ``versions`` every ``guidance_versions`` row with that key and
    hash, the newest first (by row ``id``, descending).
    """

    model_config = ConfigDict(extra="forbid")

    stamp: str
    key: str
    hash: str
    type: str
    title: str
    statement: str
    current: bool
    superseded: bool
    versions: list[GuidanceVersionOut]
