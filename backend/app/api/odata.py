"""Machine-readable OData feed for the ranked recommendation sets.

Publishes the four ranked recommendation sets (drift, excess,
stockout, changes), the planner decisions set, the excess
decisions set, the stockout decisions set and the forecast set
under ``/odata`` for machine consumers:

* ``GET /odata/`` — the OData service document,
* ``GET /odata/$metadata`` — the OData v4 CSDL metadata document, one
  EntityType per set (the scalar fields of the matching ranked row;
  list fields such as ``notes`` are joined into one ``Edm.String``),
  each keyed by ``item_code`` and ``site_code`` — the Forecast set
  additionally by ``period``,
* ``GET /odata/Drift``, ``GET /odata/Excess``, ``GET /odata/Stockout``,
  ``GET /odata/Changes`` and ``GET /odata/Forecast`` — one OData v4
  collection per set: every ranked row restricted to the properties
  the set's EntityType declares (list fields joined with ``"; "``),
  optionally filtered by ``$filter`` before ordering and paging,
  paged by ``$top`` (1 to 1000, default 1000) and ``$skip`` (0 or
  more, default 0) with ``@odata.nextLink`` (which carries the
  ``$filter`` forward) while rows remain, ordered by the full key
  tuple (item_code, site_code — plus period for the Forecast set)
  unless ``$orderby`` lists declared properties with an optional
  ``asc``, and read-only.  The Changes rows are the
  ``app.recommend.changes.recommendation_changes`` rows — one per
  ``item_code``/``site_code``, without any dedup step — each carrying
  its 1-based ``rank`` in the order that function returns them (money
  at stake first) and flattened: the four ``contributions`` values as
  ``demand_level``, ``demand_variability``, ``lead_time`` and
  ``lead_time_variability`` (null without contributions), the
  ``inputs_then`` / ``inputs_now`` values as ``d_then``, ``d_now``,
  ``lt_then`` and ``lt_now`` (null without inputs), ``unit_cost`` and
  ``money`` as doubles (a Decimal source value is converted with
  ``float()``, null when absent), and ``notes`` joined with ``"; "``
  (an empty string, never null, when there are none).  The Decisions
  rows are the current planner decision of every item-site that has
  one — the latest ``planner_decisions`` row per item-site by
  ``decided_at``, ``id`` breaking ties, the same row
  ``GET /api/drift/decisions`` reports as current — one row per
  item-site, with ``status`` ``applied`` for an accepted decision
  whose item-site's current ``item_sites.reorder_point`` is not null
  and within 1 of ``recommended_reorder_point_units``,
  ``outstanding`` for every other accepted decision and the decision
  itself otherwise, ``reorder_point_at_decision`` the stored
  ``current_reorder_point``, ``erp_reorder_point`` the item-site's
  current ``item_sites.reorder_point`` (null when the item-site row
  is missing) and no ``decided_by``, read with a fixed number of
  queries.  The ExcessDecisions rows are the current excess decision
  of every item-site that has one — the latest ``excess_decisions``
  row per item-site by ``decided_at``, ``id`` breaking ties, the same
  row ``GET /api/excess/decisions`` reports as current — one row per
  item-site, with ``status`` ``applied`` for an accepted decision
  whose item-site is no longer in the current dead-and-excess rows
  (``excess_ranked()``), ``outstanding`` for every other accepted
  decision and the decision itself otherwise,
  ``excess_units_at_decision`` the stored ``excess_units``,
  ``excess_units_now`` the item-site's current ``excess_units`` (null
  when it is no longer listed), ``money_tied_up`` the stored value
  and no ``decided_by``, the dead-and-excess ranked rows being
  computed once per request.  The StockoutDecisions rows are the
  current stockout decision of every item-site that has one — the
  latest ``stockout_decisions`` row per item-site by ``decided_at``,
  ``id`` breaking ties, the same row
  ``GET /api/stockout/decisions`` reports as current — one row per
  item-site, with ``status`` ``applied`` for an accepted decision
  whose item-site is no longer in the current stockout rows
  (``stockout_ranked()``), ``outstanding`` for every other accepted
  decision and the decision itself otherwise,
  ``p_stockout_at_decision`` the stored ``p_stockout``,
  ``p_stockout_now`` the item-site's current ``p_stockout`` (null
  when it is no longer listed), ``money_at_risk`` the stored value
  and no ``decided_by``, the stockout ranked rows being computed
  once per request.  The Forecast rows are one row per item-site per
  forecast bucket, from ``app.forecast.listing.forecast_rows``
  (computed once per request): the item-site's ``as_of``,
  ``demand_class`` and the winning ``method``, the bucket's 1-based
  ``period`` (1 to 12), ``bucket_start`` and ``bucket_end``, the
  statistical ``quantity`` — never changed by an override [rule:
  OVR-4] — beside ``effective_quantity`` and ``override_days``, and
  ``backtest_mae`` the winning method's backtest MAE (null when no
  backtest ran), ordered by item code, site code and period.  A
  ``$filter``
  is one of ``<P> gt <L>``,
  ``<P> ge <L>``, ``<P> eq <L>`` or ``<P> gt <L> or <P> eq <L>`` with
  the same property both times, where ``<P>`` is a property the set's
  EntityType declares as ``Edm.Date`` or ``Edm.DateTimeOffset`` and
  ``<L>`` an ISO date (``YYYY-MM-DD``) or timestamp (``YYYY-
  MM-DDTHH:MM:SS`` with an optional fractional part and an optional
  ``Z`` or ``±HH:MM``); for an ``Edm.Date`` property the comparison
  uses the literal's date part, and rows whose property is null never
  match.  ``$format=json`` is accepted and ignored; any other option,
  and any ``$filter`` outside those shapes (notably ``$select`` and
  ``$expand``), answers 400, and an out-of-bounds ``$top``/``$skip``
  value answers 400 in the OData v4 error format.  A ``ValueError``
  from a ranked function answers 400 — in the OData v4 error format
  for the Changes set, as the ``{"detail": ...}`` body for the other
  sets.

Unlike the user-facing ``/api`` routers, this feed authenticates with
an HTTP Basic machine credential taken from the environment
(``AIERP_ODATA_USER`` / ``AIERP_ODATA_PASSWORD``), read at call time
and compared in constant time.  The feed fails closed: while either
variable is unset or empty every route answers 503, and the
credential is never logged.  The OpenAPI schema advertises the feed
under the ``basicAuth`` security scheme (``http``/``basic``) beside
``bearerAuth``: every ``/odata`` operation declares
``security: [{"basicAuth": []}]``.  That scheme documents the machine
credential only — the dependency behind it never raises and performs
no check of its own.
"""

# parity: backend-only the OData feed for BI tools and other machine consumers

import base64
import binascii
import hmac
import logging
import os
import re
import types
import xml.etree.ElementTree as ElementTree
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal, Union, get_args, get_origin
from urllib.parse import quote

from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    Security,
)
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBasic
from pydantic import BaseModel

from app.api.drift import DriftRow
from app.api.excess import ExcessRow
from app.api.stockout import StockoutRow
from app.forecast.listing import forecast_rows
from app.recommend.changes import recommendation_changes
from app.recommend.drift import drift_ranked
from app.recommend.excess import excess_ranked
from app.recommend.stockout import stockout_ranked
from app.repositories.excess_decisions_postgres_repository import (
    ExcessDecisionPostgresRepository,
)
from app.repositories.planner_decisions_postgres_repository import (
    PlannerDecisionPostgresRepository,
)
from app.repositories.stockout_decisions_postgres_repository import (
    StockoutDecisionPostgresRepository,
)

logger = logging.getLogger(__name__)

_ODATA_USER_ENV = "AIERP_ODATA_USER"
_ODATA_PASSWORD_ENV = "AIERP_ODATA_PASSWORD"
_ODATA_REALM = 'Basic realm="AIERP OData"'
_NOT_CONFIGURED = "OData feed not configured"
_EDM_NAMESPACE = "http://docs.oasis-open.org/odata/ns/edm"
_CSDL_NAMESPACE = "AIERP"
_CONTAINER_NAME = "AIERPService"
_KEY_PROPERTIES = ("item_code", "site_code")


class ForecastRow(BaseModel):
    """One item-site's one forecast bucket, as the feed exposes it.

    The properties ``GET /odata/Forecast`` exposes for one forecast
    bucket of one ``app.forecast.listing.forecast_rows`` row: the
    item-site's ``as_of``, ``demand_class`` and the winning
    ``method``, the bucket's 1-based ``period`` (1 to 12),
    ``bucket_start`` and ``bucket_end``, the statistical
    ``quantity`` — never changed by an override [rule: OVR-4] —
    beside the effective forecast's ``effective_quantity`` and
    ``override_days``, and ``backtest_mae`` the winning method's
    backtest MAE (null when no backtest ran).
    """

    item_code: str
    site_code: str
    as_of: date
    demand_class: str
    method: str
    period: int
    bucket_start: date
    bucket_end: date
    quantity: float
    effective_quantity: float
    override_days: int
    backtest_mae: float | None = None


class ChangesRow(BaseModel):
    """One item-site's reorder-point change row, flattened for the feed.

    The properties ``GET /odata/Changes`` exposes for one
    ``app.recommend.changes.recommendation_changes`` row: the 1-based
    ``rank`` in the order that function returns the rows (money at
    stake first), the CSV columns (``notes`` joined with ``"; "``, an
    empty string when there are none), the four ``contributions``
    values flattened onto ``demand_level``, ``demand_variability``,
    ``lead_time`` and ``lead_time_variability`` (null without them)
    and the ``inputs_then`` / ``inputs_now`` values flattened onto
    ``d_then``, ``d_now``, ``lt_then`` and ``lt_now`` (null without
    them).  ``unit_cost`` and ``money`` are doubles: a Decimal source
    value is converted with ``float()``, a missing value is null.
    """

    rank: int
    item_code: str
    site_code: str
    status_then: str
    status_now: str
    reorder_point_units_then: int | None = None
    reorder_point_units_now: int | None = None
    change_units: int | None = None
    primary_driver: str | None = None
    unit_cost: float | None = None
    money: float | None = None
    since: date
    as_of: date
    demand_level: float | None = None
    demand_variability: float | None = None
    lead_time: float | None = None
    lead_time_variability: float | None = None
    d_then: float | None = None
    d_now: float | None = None
    lt_then: float | None = None
    lt_now: float | None = None
    notes: list[str]


class DecisionRow(BaseModel):
    """One item-site's current planner decision, as the feed exposes it.

    The properties ``GET /odata/Decisions`` exposes for one item-site's
    current planner decision (the latest ``planner_decisions`` row by
    ``decided_at``, ``id`` breaking ties — the same row
    ``GET /api/drift/decisions`` reports as current): the decision
    kind, its ``status`` (``applied`` for an accepted decision whose
    item-site's current ``item_sites.reorder_point`` is not null and
    within 1 of ``recommended_reorder_point_units``, ``outstanding``
    for every other accepted decision, and the decision itself
    otherwise), the snapshot (``reason``, ``snooze_until``, ``as_of``,
    ``recommended_reorder_point_units``), ``reorder_point_at_decision``
    (the stored ``current_reorder_point``), ``erp_reorder_point`` (the
    item-site's current ``item_sites.reorder_point``, null when the
    item-site row is missing), ``money_at_stake`` and ``decided_at``.
    ``decided_by`` is not exposed.
    """

    item_code: str
    site_code: str
    decision: str
    status: str
    reason: str | None = None
    snooze_until: date | None = None
    as_of: date
    recommended_reorder_point_units: int
    reorder_point_at_decision: float | None = None
    erp_reorder_point: float | None = None
    money_at_stake: float | None = None
    decided_at: datetime


class ExcessDecisionRow(BaseModel):
    """One item-site's current excess decision, as the feed exposes it.

    The properties ``GET /odata/ExcessDecisions`` exposes for one
    item-site's current excess decision (the latest
    ``excess_decisions`` row by ``decided_at``, ``id`` breaking ties
    — the same row ``GET /api/excess/decisions`` reports as current):
    the decision kind and its ``action``, its ``status`` (``applied``
    for an accepted decision whose item-site is no longer in the
    current dead-and-excess rows, ``outstanding`` for every other
    accepted decision, and the decision itself otherwise), the
    snapshot (``reason``, ``snooze_until``, ``as_of``, ``category``),
    ``excess_units_at_decision`` (the stored ``excess_units``),
    ``excess_units_now`` (the item-site's current ``excess_units``
    from the dead-and-excess ranked rows, null when it is no longer
    listed) and ``money_tied_up`` (the stored value).  ``decided_by``
    is not exposed.
    """

    item_code: str
    site_code: str
    decision: str
    action: str | None = None
    status: str
    reason: str | None = None
    snooze_until: date | None = None
    as_of: date
    category: str
    excess_units_at_decision: float
    excess_units_now: float | None = None
    money_tied_up: float | None = None
    decided_at: datetime


class StockoutDecisionRow(BaseModel):
    """One item-site's current stockout decision, as the feed exposes it.

    The properties ``GET /odata/StockoutDecisions`` exposes for one
    item-site's current stockout decision (the latest
    ``stockout_decisions`` row by ``decided_at``, ``id`` breaking ties
    — the same row ``GET /api/stockout/decisions`` reports as
    current): the decision kind and its ``action``, its ``status``
    (``applied`` for an accepted decision whose item-site is no
    longer in the current stockout rows (``stockout_ranked()``),
    ``outstanding`` for every other accepted decision, and the
    decision itself otherwise), the snapshot (``reason``,
    ``snooze_until``, ``as_of``, ``category``),
    ``p_stockout_at_decision`` (the stored ``p_stockout``),
    ``p_stockout_now`` (the item-site's current ``p_stockout`` from
    the stockout ranked rows, null when it is no longer listed) and
    ``money_at_risk`` (the stored value).  ``decided_by`` is not
    exposed.
    """

    item_code: str
    site_code: str
    decision: str
    action: str | None = None
    status: str
    reason: str | None = None
    snooze_until: date | None = None
    as_of: date
    category: str
    p_stockout_at_decision: float
    p_stockout_now: float | None = None
    money_at_risk: float | None = None
    decided_at: datetime


# One entry per ranked set, in the order the service document lists
# them.  The row model is the documented field shape of the matching
# ranked row (app.recommend.drift.drift_ranked,
# app.recommend.excess.excess_ranked,
# app.recommend.stockout.stockout_ranked, the flattened
# recommendation_changes row for Changes, and the flattened current
# planner decision row for Decisions, and the flattened current
# excess decision row for ExcessDecisions, and the flattened current
# stockout decision row for StockoutDecisions, and the flattened
# forecast bucket row for Forecast).
_SETS: list[tuple[str, type[BaseModel]]] = [
    ("Drift", DriftRow),
    ("Excess", ExcessRow),
    ("Stockout", StockoutRow),
    ("Changes", ChangesRow),
    ("Decisions", DecisionRow),
    ("ExcessDecisions", ExcessDecisionRow),
    ("StockoutDecisions", StockoutDecisionRow),
    ("Forecast", ForecastRow),
]


def _set_key_properties(set_name: str) -> tuple[str, ...]:
    """The key properties of one entity set, in document order.

    Every set is keyed by ``item_code`` and ``site_code``; the
    Forecast set — one row per item-site per forecast bucket — is
    keyed by those plus ``period``.
    """
    if set_name == "Forecast":
        return (*_KEY_PROPERTIES, "period")
    return _KEY_PROPERTIES


def _unauthorized() -> HTTPException:
    """The 401 for missing or wrong Basic credentials, with the realm."""
    return HTTPException(
        status_code=401,
        detail="Invalid OData machine credential.",
        headers={"WWW-Authenticate": _ODATA_REALM},
    )


def _not_configured() -> HTTPException:
    """The 503 served while the feed is not configured."""
    return HTTPException(status_code=503, detail=_NOT_CONFIGURED)


def _configured_credential() -> tuple[str, str] | None:
    """The machine credential from the environment, read at call time.

    Returns ``(user, password)`` or ``None`` while either variable is
    unset or empty (fail closed).
    """
    user = os.environ.get(_ODATA_USER_ENV)
    password = os.environ.get(_ODATA_PASSWORD_ENV)
    if not user or not password:
        return None
    return user, password


def require_odata_credential(
    authorization: str | None = Header(default=None),
) -> None:
    """HTTP Basic machine-credential gate for every ``/odata`` route.

    The credential is read from the environment at call time and
    compared in constant time.  A missing header, a non-Basic scheme
    (such as a user Bearer token), a malformed Base64 payload, or a
    wrong user or password all answer 401 with
    ``WWW-Authenticate: Basic realm="AIERP OData"``.  While either
    environment variable is unset or empty the gate answers 503
    "OData feed not configured" (fail closed).  The credential is
    never logged.
    """
    credential = _configured_credential()
    if credential is None:
        logger.warning(
            "odata: feed not configured — %s and %s must both be set",
            _ODATA_USER_ENV,
            _ODATA_PASSWORD_ENV,
        )
        raise _not_configured()

    if authorization is None:
        logger.warning("odata: rejected request without an Authorization header")
        raise _unauthorized()

    scheme, _, payload = authorization.partition(" ")
    if scheme.lower() != "basic" or not payload:
        logger.warning("odata: rejected non-Basic Authorization header")
        raise _unauthorized()

    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        logger.warning("odata: rejected malformed Basic credential encoding")
        raise _unauthorized()

    presented_user, _, presented_password = decoded.partition(":")
    expected_user, expected_password = credential
    user_matches = hmac.compare_digest(
        presented_user.encode("utf-8"), expected_user.encode("utf-8")
    )
    password_matches = hmac.compare_digest(
        presented_password.encode("utf-8"), expected_password.encode("utf-8")
    )
    if not (user_matches and password_matches):
        logger.warning("odata: rejected wrong Basic credential")
        raise _unauthorized()


def _edm_type(annotation: Any) -> tuple[str, bool]:
    """The ``(EDM type name, nullable)`` pair for one row-model field.

    Scalar fields map to their EDM type; a list field such as
    ``notes`` is joined into a single ``Edm.String``; ``Literal``
    categories are strings.
    """
    nullable = False
    origin = get_origin(annotation)
    if origin is Union or origin is types.UnionType:
        members = [
            member for member in get_args(annotation) if member is not type(None)
        ]
        nullable = len(members) != len(get_args(annotation))
        annotation = members[0] if members else str
        origin = get_origin(annotation)
    if origin is Literal:
        return "Edm.String", nullable
    if origin in (list, tuple, set, frozenset):
        return "Edm.String", nullable
    if annotation is str:
        return "Edm.String", nullable
    if annotation is bool:
        return "Edm.Boolean", nullable
    if annotation is int:
        return "Edm.Int32", nullable
    if annotation is float:
        return "Edm.Double", nullable
    if annotation is date:
        return "Edm.Date", nullable
    if annotation is datetime:
        return "Edm.DateTimeOffset", nullable
    if annotation is bytes:
        return "Edm.Binary", nullable
    raise TypeError(f"no EDM mapping for field annotation {annotation!r}")


def _metadata_xml() -> str:
    """The CSDL document for the eight entity sets."""
    ElementTree.register_namespace("", _EDM_NAMESPACE)
    root = ElementTree.Element(f"{{{_EDM_NAMESPACE}}}Edm", {"Version": "4.0"})
    schema = ElementTree.SubElement(
        root, f"{{{_EDM_NAMESPACE}}}Schema", {"Namespace": _CSDL_NAMESPACE}
    )
    for set_name, row_model in _SETS:
        key_properties = _set_key_properties(set_name)
        entity = ElementTree.SubElement(
            schema, f"{{{_EDM_NAMESPACE}}}EntityType", {"Name": set_name}
        )
        key = ElementTree.SubElement(entity, f"{{{_EDM_NAMESPACE}}}Key")
        for key_name in key_properties:
            ElementTree.SubElement(
                key, f"{{{_EDM_NAMESPACE}}}PropertyRef", {"Name": key_name}
            )
        field_names = [
            *key_properties,
            *(name for name in row_model.model_fields if name not in key_properties),
        ]
        for field_name in field_names:
            edm_type, nullable = _edm_type(
                row_model.model_fields[field_name].annotation
            )
            attributes = {"Name": field_name, "Type": edm_type}
            if nullable:
                attributes["Nullable"] = "true"
            ElementTree.SubElement(entity, f"{{{_EDM_NAMESPACE}}}Property", attributes)
    container = ElementTree.SubElement(
        schema, f"{{{_EDM_NAMESPACE}}}EntityContainer", {"Name": _CONTAINER_NAME}
    )
    for set_name, _ in _SETS:
        ElementTree.SubElement(
            container,
            f"{{{_EDM_NAMESPACE}}}EntitySet",
            {"Name": set_name, "EntityType": f"{_CSDL_NAMESPACE}.{set_name}"},
        )
    ElementTree.indent(root)
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        + ElementTree.tostring(root, encoding="unicode")
        + "\n"
    )


_METADATA_XML = _metadata_xml()

router = APIRouter(
    prefix="/odata",
    tags=["odata"],
    dependencies=[
        Depends(require_odata_credential),
        # Documentation only: advertises the machine Basic credential
        # to OpenAPI consumers as the ``basicAuth`` scheme.  With
        # auto_error=False it never raises and never changes a
        # response — require_odata_credential is the only gate.
        Security(HTTPBasic(auto_error=False, scheme_name="basicAuth")),
    ],
)


@router.get("/")
def service_document() -> dict:
    """The OData service document listing the eight entity sets."""
    return {
        "@odata.context": "$metadata",
        "value": [
            {"name": set_name, "kind": "EntitySet", "url": set_name}
            for set_name, _ in _SETS
        ],
    }


@router.get("/$metadata")
def metadata() -> Response:
    """The OData v4 CSDL metadata document for the eight entity sets."""
    return Response(content=_METADATA_XML, media_type="application/xml")


# --- The ranked collection endpoints ----------------------------------------

#: The query options the collection endpoints understand.  Every other
#: option (notably ``$select`` and ``$expand``) is unsupported and
#: answers 400 "unsupported query option".
_SUPPORTED_OPTIONS = frozenset({"$top", "$skip", "$format", "$orderby", "$filter"})

_TOP_MIN = 1
_TOP_MAX = 1000
_TOP_DEFAULT = 1000
_SKIP_MIN = 0
_SKIP_DEFAULT = 0


def _flatten_changes_row(row: dict, rank: int) -> dict:
    """One raw change row as the flat row the Changes set exposes.

    Carries the 1-based ``rank`` in the order
    ``recommendation_changes`` returned the rows (money at stake
    first); flattens ``contributions`` onto the four contribution
    properties and ``inputs_then`` / ``inputs_now`` onto
    ``d_then`` / ``d_now`` / ``lt_then`` / ``lt_now`` (null without
    them); converts ``unit_cost`` and ``money`` with ``float()``
    (null when absent); joins ``notes`` with ``"; "`` — an empty
    string, never null, when there are none, matching the flat CSV
    export column.
    """
    inputs_then = row.get("inputs_then")
    inputs_now = row.get("inputs_now")
    contributions = row.get("contributions")
    unit_cost = row.get("unit_cost")
    money = row.get("money")
    return {
        "rank": rank,
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "status_then": row["status_then"],
        "status_now": row["status_now"],
        "reorder_point_units_then": row.get("reorder_point_units_then"),
        "reorder_point_units_now": row.get("reorder_point_units_now"),
        "change_units": row.get("change_units"),
        "primary_driver": row.get("primary_driver"),
        "unit_cost": float(unit_cost) if unit_cost is not None else None,
        "money": float(money) if money is not None else None,
        "since": row["since"],
        "as_of": row["as_of"],
        "demand_level": (
            contributions.get("demand_level") if contributions is not None else None
        ),
        "demand_variability": (
            contributions.get("demand_variability")
            if contributions is not None
            else None
        ),
        "lead_time": (
            contributions.get("lead_time") if contributions is not None else None
        ),
        "lead_time_variability": (
            contributions.get("lead_time_variability")
            if contributions is not None
            else None
        ),
        "d_then": inputs_then.get("d") if inputs_then is not None else None,
        "d_now": inputs_now.get("d") if inputs_now is not None else None,
        "lt_then": inputs_then.get("lt") if inputs_then is not None else None,
        "lt_now": inputs_now.get("lt") if inputs_now is not None else None,
        "notes": "; ".join(str(note) for note in row.get("notes", ())),
    }


def _changes_ranked() -> list[dict]:
    """The Changes rows: the raw change rows, flattened and ranked.

    One row per ``item_code``/``site_code`` exactly as
    ``recommendation_changes`` returns them (its pairing is the
    dedup guarantee; the feed applies no dedup step of its own), each
    with its 1-based ``rank`` in that order — money at stake first.
    """
    return [
        _flatten_changes_row(row, rank)
        for rank, row in enumerate(recommendation_changes(), start=1)
    ]


_repo = PlannerDecisionPostgresRepository()


def _decision_status(
    decision: str,
    recommended_reorder_point_units: int,
    erp_reorder_point: float | None,
) -> str:
    """The Decisions set's ``status`` for one current decision.

    ``applied`` for an accepted decision whose item-site's current
    ``item_sites.reorder_point`` is not null and within 1 of
    ``recommended_reorder_point_units``; ``outstanding`` for every
    other accepted decision (including one whose item-site row is
    missing); the decision itself for a dismissal or a snooze.
    """
    if decision != "accepted":
        return decision
    if (
        erp_reorder_point is not None
        and abs(erp_reorder_point - recommended_reorder_point_units) <= 1
    ):
        return "applied"
    return "outstanding"


def _flatten_decision_row(row: dict) -> dict:
    """One current decision row as the flat row the Decisions set exposes.

    Carries the decision kind and its ``status`` (``_decision_status``
    against the item-site's current ``erp_reorder_point``), the
    snapshot columns, ``reorder_point_at_decision`` (the stored
    ``current_reorder_point``), ``erp_reorder_point`` (the item-site's
    current ``item_sites.reorder_point``, null when the item-site row
    is missing) and ``money_at_stake`` / ``decided_at``.  The stored
    ``decided_by`` is dropped: it is not exposed by the feed.
    """
    recommended = row["recommended_reorder_point_units"]
    return {
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "decision": row["decision"],
        "status": _decision_status(
            row["decision"], recommended, row["erp_reorder_point"]
        ),
        "reason": row["reason"],
        "snooze_until": row["snooze_until"],
        "as_of": row["as_of"],
        "recommended_reorder_point_units": recommended,
        "reorder_point_at_decision": row["current_reorder_point"],
        "erp_reorder_point": row["erp_reorder_point"],
        "money_at_stake": row["money_at_stake"],
        "decided_at": row["decided_at"],
    }


def _decisions_ranked() -> list[dict]:
    """The Decisions rows: the current planner decision per item-site.

    One row per item-site that has a planner decision: the item-site's
    current decision (the latest by decided_at, id breaking ties —
    the same row ``GET /api/drift/decisions`` reports as current),
    flattened by ``_flatten_decision_row``.  The rows are read with a
    fixed number of queries whatever the number of item-sites.
    """
    return [_flatten_decision_row(row) for row in _repo.current_decisions()]


_excess_repo = ExcessDecisionPostgresRepository()


def _excess_decision_status(decision: str, listed_now: bool) -> str:
    """The ExcessDecisions set's ``status`` for one current decision.

    ``applied`` for an accepted decision whose item-site is no longer
    in the current dead-and-excess rows, ``outstanding`` for every
    other accepted decision, and the decision itself for a dismissal
    or a snooze.
    """
    if decision != "accepted":
        return decision
    return "outstanding" if listed_now else "applied"


def _flatten_excess_decision_row(row: dict, ranked_by_key: dict) -> dict:
    """One current excess decision row as the flat row the
    ExcessDecisions set exposes.

    Carries the decision kind and its ``action``, its ``status``
    (``_excess_decision_status`` against the item-site's presence in
    the current dead-and-excess rows), the snapshot columns
    (``reason``, ``snooze_until``, ``as_of``, ``category``),
    ``excess_units_at_decision`` (the stored ``excess_units``),
    ``excess_units_now`` (the item-site's current ``excess_units``,
    null when it is no longer listed) and the stored
    ``money_tied_up`` / ``decided_at``.  The stored ``decided_by`` is
    dropped: it is not exposed by the feed.
    """
    ranked = ranked_by_key.get((row["item_code"], row["site_code"]))
    return {
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "decision": row["decision"],
        "action": row["action"],
        "status": _excess_decision_status(row["decision"], ranked is not None),
        "reason": row["reason"],
        "snooze_until": row["snooze_until"],
        "as_of": row["as_of"],
        "category": row["category"],
        "excess_units_at_decision": row["excess_units"],
        "excess_units_now": (ranked["excess_units"] if ranked is not None else None),
        "money_tied_up": row["money_tied_up"],
        "decided_at": row["decided_at"],
    }


def _excess_decisions_ranked() -> list[dict]:
    """The ExcessDecisions rows: the current excess decision per item-site.

    One row per item-site that has an excess decision: the item-site's
    current decision (the latest by decided_at, id breaking ties —
    the same row ``GET /api/excess/decisions`` reports as current),
    flattened by ``_flatten_excess_decision_row``.  The dead-and-excess
    ranked rows are computed once per request, whatever the number of
    item-sites.
    """
    ranked_by_key = {
        (row["item_code"], row["site_code"]): row for row in excess_ranked()
    }
    return [
        _flatten_excess_decision_row(row, ranked_by_key)
        for row in _excess_repo.current_all()
    ]


_stockout_repo = StockoutDecisionPostgresRepository()


def _stockout_decision_status(decision: str, listed_now: bool) -> str:
    """The StockoutDecisions set's ``status`` for one current decision.

    ``applied`` for an accepted decision whose item-site is no longer
    in the current stockout rows, ``outstanding`` for every other
    accepted decision, and the decision itself for a dismissal or a
    snooze.
    """
    if decision != "accepted":
        return decision
    return "outstanding" if listed_now else "applied"


def _flatten_stockout_decision_row(row: dict, ranked_by_key: dict) -> dict:
    """One current stockout decision row as the flat row the
    StockoutDecisions set exposes.

    Carries the decision kind and its ``action``, its ``status``
    (``_stockout_decision_status`` against the item-site's presence in
    the current stockout rows), the snapshot columns (``reason``,
    ``snooze_until``, ``as_of``, ``category``),
    ``p_stockout_at_decision`` (the stored ``p_stockout``),
    ``p_stockout_now`` (the item-site's current ``p_stockout``, null
    when it is no longer listed) and the stored ``money_at_risk`` /
    ``decided_at``.  The stored ``decided_by`` is dropped: it is not
    exposed by the feed.
    """
    ranked = ranked_by_key.get((row["item_code"], row["site_code"]))
    return {
        "item_code": row["item_code"],
        "site_code": row["site_code"],
        "decision": row["decision"],
        "action": row["action"],
        "status": _stockout_decision_status(row["decision"], ranked is not None),
        "reason": row["reason"],
        "snooze_until": row["snooze_until"],
        "as_of": row["as_of"],
        "category": row["category"],
        "p_stockout_at_decision": row["p_stockout"],
        "p_stockout_now": ranked["p_stockout"] if ranked is not None else None,
        "money_at_risk": row["money_at_risk"],
        "decided_at": row["decided_at"],
    }


def _stockout_decisions_ranked() -> list[dict]:
    """The StockoutDecisions rows: the current stockout decision per
    item-site.

    One row per item-site that has a stockout decision: the
    item-site's current decision (the latest by decided_at, id
    breaking ties — the same row ``GET /api/stockout/decisions``
    reports as current), flattened by
    ``_flatten_stockout_decision_row``.  The stockout ranked rows are
    computed once per request, whatever the number of item-sites.
    """
    ranked_by_key = {
        (row["item_code"], row["site_code"]): row for row in stockout_ranked()
    }
    return [
        _flatten_stockout_decision_row(row, ranked_by_key)
        for row in _stockout_repo.current_all()
    ]


def _forecast_ranked() -> list[dict]:
    """The Forecast rows: one row per item-site per forecast bucket.

    Every item-site's forecast from ``forecast_rows()`` — computed
    once per request, whatever the number of item-sites — flattened
    onto its 12 horizon buckets: the bucket's 1-based ``period`` (1
    to 12), its ``bucket_start`` / ``bucket_end``, the statistical
    ``quantity`` — never changed by an override [rule: OVR-4] —
    beside the effective forecast's ``effective_quantity`` and
    ``override_days``, the item-site's ``as_of``, ``demand_class``
    and the winning ``method``, and ``backtest_mae`` the winning
    method's backtest MAE (null when no backtest ran).
    """
    rows: list[dict] = []
    for row in forecast_rows():
        backtest = row["backtest"]
        backtest_mae = backtest["mae"] if backtest is not None else None
        for period, bucket in enumerate(row["forecast"], start=1):
            rows.append(
                {
                    "item_code": row["item_code"],
                    "site_code": row["site_code"],
                    "as_of": row["as_of"],
                    "demand_class": row["demand_class"],
                    "method": row["method"],
                    "period": period,
                    "bucket_start": bucket["bucket_start"],
                    "bucket_end": bucket["bucket_end"],
                    "quantity": bucket["quantity"],
                    "effective_quantity": bucket["effective_quantity"],
                    "override_days": bucket["override_days"],
                    "backtest_mae": backtest_mae,
                }
            )
    return rows


_RANKED_FUNCTIONS = {
    "Drift": drift_ranked,
    "Excess": excess_ranked,
    "Stockout": stockout_ranked,
    "Changes": _changes_ranked,
    "Decisions": _decisions_ranked,
    "ExcessDecisions": _excess_decisions_ranked,
    "StockoutDecisions": _stockout_decisions_ranked,
    "Forecast": _forecast_ranked,
}

_NONNEGATIVE_DECIMAL = re.compile(r"[0-9]+")

#: The longest digit string we convert with ``int``; Python refuses
#: longer ones (``sys.set_int_max_str_digits``), and a longer value is
#: far beyond any page this feed serves.
_INT_DIGIT_LIMIT = 9


def _odata_error(message: str, target: str | None = None) -> JSONResponse:
    """A 400 in the OData v4 error format for a bad query option."""
    error: dict[str, str] = {"code": "BadRequest", "message": message}
    if target is not None:
        error["target"] = target
    return JSONResponse(status_code=400, content={"error": error})


def _parse_page_value(raw: str, minimum: int, maximum: int | None) -> int | None:
    """A plain decimal integer within bounds, or ``None``.

    Only an unsigned base-10 digit string counts: signed, fractional,
    blank, underscore, unicode-digit or control-character input is
    rejected so no hostile value can reach the slicing.  A digit
    string beyond ``_INT_DIGIT_LIMIT`` is never converted (Python's
    ``int`` refuses very long strings): with a maximum it is out of
    range, without one it skips past every row.  Leading zeros carry
    no weight (``007`` parses as ``7``), so the length guard applies
    to the value, never to its spelling.
    """
    if _NONNEGATIVE_DECIMAL.fullmatch(raw) is None:
        return None
    digits = raw.lstrip("0") or "0"
    if len(digits) > _INT_DIGIT_LIMIT:
        if maximum is None:
            return 10**_INT_DIGIT_LIMIT
        return None
    value = int(digits)
    if value < minimum or (maximum is not None and value > maximum):
        return None
    return value


def _parse_orderby(raw: str, declared: set[str]) -> list[str] | None:
    """The ``$orderby`` property list, or ``None`` when the option is
    not the supported shape: a comma-separated list of declared
    property names, each with an optional ``asc`` (``desc`` and
    anything else fail)."""
    parts = [part.strip() for part in raw.split(",")]
    if not parts or "" in parts:
        return None
    properties: list[str] = []
    for part in parts:
        tokens = part.split()
        if len(tokens) > 2 or tokens[0] not in declared:
            return None
        if len(tokens) == 2 and tokens[1] != "asc":
            return None
        properties.append(tokens[0])
    return properties


def _ordered_rows(
    rows: list[dict],
    model: type[BaseModel],
    properties: list[str] | None,
    key_properties: tuple[str, ...] = _KEY_PROPERTIES,
) -> list[dict]:
    """The projected rows in a total, stable order.

    The rows are first projected to the properties the set's
    EntityType in $metadata declares — list fields joined into their
    single ``Edm.String`` — so ``$orderby`` sorts by exactly the value
    the response exposes, never by a raw list.  Without
    ``properties`` (no ``$orderby``) the rows sort by the set's full
    key tuple (item_code, site_code — plus period for the Forecast
    set); with it, by the listed fields ascending with nulls first.
    The key tuple is always the final tie-breaker, so ``$top``/
    ``$skip`` pages never overlap or skip a row.
    """
    projected = [_project_row(row, model) for row in rows]

    def sort_key(row: dict) -> tuple:
        head = tuple(
            (0, "") if row.get(name) is None else (1, row.get(name))
            for name in properties or ()
        )
        return head + tuple(row.get(name) for name in key_properties)

    return sorted(projected, key=sort_key)


def _project_row(row: dict, model: type[BaseModel]) -> dict:
    """One ranked row restricted to the properties $metadata declares.

    List fields (such as ``notes``) are joined into the single string
    $metadata declares for them, with items separated by ``"; "``;
    dates and datetimes are written as their ISO 8601 strings.
    """
    data: dict[str, Any] = {}
    for name in model.model_fields:
        value = row.get(name)
        if isinstance(value, (list, tuple, set, frozenset)):
            value = "; ".join(str(item) for item in value)
        elif isinstance(value, (datetime, date)):
            value = value.isoformat()
        data[name] = value
    return data


# --- The $filter option: the supported shapes ------------------------------

#: ISO date literal: ``YYYY-MM-DD``.
_DATE_LITERAL_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
#: ISO timestamp literal: ``YYYY-MM-DDTHH:MM:SS`` with an optional
#: fractional part and an optional ``Z`` or ``±HH:MM`` offset.
_TIMESTAMP_LITERAL_RE = re.compile(
    r"([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2}):([0-9]{2})"
    r"(\.[0-9]+)?"
    r"(Z|[+-][0-9]{2}:[0-9]{2})?"
)

_FILTER_OPERATORS = ("gt", "ge", "eq")


def _parse_literal(raw: str) -> tuple[date, datetime | None] | None:
    """A valid ISO date or timestamp literal, or ``None`` when malformed.

    Returns ``(the literal's date part, the moment)``: for a plain
    date the moment is ``None``, for a timestamp both are given (the
    moment naive when the literal carries no offset).  Only the exact
    spellings the grammar allows count: a calendar-invalid date (such
    as ``2026-02-30``), an out-of-range time, an offset of 24 hours or
    more, an offset with minutes of 60 or more (such as ``+00:75``),
    quotes or any other spelling is malformed.
    """
    match = _DATE_LITERAL_RE.fullmatch(raw)
    if match is not None:
        try:
            day = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
        return day, None
    match = _TIMESTAMP_LITERAL_RE.fullmatch(raw)
    if match is None:
        return None
    microsecond = 0
    if match.group(7) is not None:
        microsecond = int(match.group(7)[1:7].ljust(6, "0"))
    zone = match.group(8)
    if zone is None:
        tzinfo: timezone | None = None
    elif zone == "Z":
        tzinfo = UTC
    else:
        offset_minutes = int(zone[4:6])
        if offset_minutes >= 60:
            return None
        offset = timedelta(hours=int(zone[1:3]), minutes=offset_minutes)
        if zone[0] == "-":
            offset = -offset
        if abs(offset) >= timedelta(hours=24):
            return None
        tzinfo = timezone(offset)
    try:
        moment = datetime(
            int(match.group(1)),
            int(match.group(2)),
            int(match.group(3)),
            int(match.group(4)),
            int(match.group(5)),
            int(match.group(6)),
            microsecond,
            tzinfo,
        )
    except ValueError:
        return None
    return moment.date(), moment


def _parse_filter(
    raw: str, filterable: dict[str, str]
) -> tuple[tuple[str, str, tuple[date, datetime | None], str], ...] | None:
    """The supported ``$filter`` shapes, or ``None`` when unsupported.

    Exactly one of: a single ``<P> gt <L>`` / ``<P> ge <L>`` /
    ``<P> eq <L>`` comparison, or ``<P> gt <L> or <P> eq <L>`` with
    the same property both times, where ``<P>`` is a property the set
    declares as ``Edm.Date`` or ``Edm.DateTimeOffset`` (``filterable``
    maps it to that type) and ``<L>`` a valid ISO date or timestamp
    literal.  Anything else — another operator, another property,
    ``and``, a function, a quoted or malformed literal, stray tokens —
    is unsupported.  Each clause is ``(property, operator, (the
    literal's date part, the literal's moment), the EDM type)``.
    """
    tokens = raw.split()

    def _comparison(
        start: int, allowed: tuple[str, ...]
    ) -> tuple[str, str, tuple[date, datetime | None], str] | None:
        property_name, operator, literal = tokens[start : start + 3]
        edm_type = filterable.get(property_name)
        if edm_type is None or operator not in allowed:
            return None
        parsed = _parse_literal(literal)
        if parsed is None:
            return None
        return (property_name, operator, parsed, edm_type)

    if len(tokens) == 3:
        clause = _comparison(0, _FILTER_OPERATORS)
        return (clause,) if clause is not None else None
    if len(tokens) == 7 and tokens[3] == "or":
        first = _comparison(0, ("gt",))
        second = _comparison(4, ("eq",))
        if first is None or second is None or first[0] != second[0]:
            return None
        return (first, second)
    return None


def _as_utc(moment: datetime) -> datetime:
    """The instant in UTC, reading a naive moment as already UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _clause_matches(
    row: dict, clause: tuple[str, str, tuple[date, datetime | None], str]
) -> bool:
    """Whether one raw row satisfies one (property, operator, literal)
    clause.

    Rows whose property is null — or missing — never match.  For an
    ``Edm.Date`` property the comparison uses the literal's date part;
    for an ``Edm.DateTimeOffset`` property both sides are compared as
    UTC instants (a naive value or literal is read as UTC, and a plain
    date literal as midnight).  A value of a type the property does
    not declare compares as a non-match, never as an error.
    """
    property_name, operator, (literal_date, literal_moment), edm_type = clause
    value = row.get(property_name)
    if value is None:
        return False
    if edm_type == "Edm.Date":
        if isinstance(value, datetime):
            value = value.date()
        if not isinstance(value, date):
            return False
        left, right = value, literal_date
    else:
        if isinstance(value, datetime):
            left = value
        elif isinstance(value, date):
            left = datetime(value.year, value.month, value.day)
        else:
            return False
        if literal_moment is None:
            literal_moment = datetime(
                literal_date.year, literal_date.month, literal_date.day
            )
        right = literal_moment
        left, right = _as_utc(left), _as_utc(right)
    if operator == "gt":
        return left > right
    if operator == "ge":
        return left >= right
    return left == right


def _apply_filter(
    rows: list[dict],
    clauses: tuple[tuple[str, str, tuple[date, datetime | None], str], ...],
) -> list[dict]:
    """The rows matching at least one clause (the clauses are ORed)."""
    return [
        row for row in rows if any(_clause_matches(row, clause) for clause in clauses)
    ]


def _filterable_properties(model: type[BaseModel]) -> dict[str, str]:
    """The properties a ``$filter`` may name for one set, by EDM type.

    The properties the set's EntityType declares as ``Edm.Date`` or
    ``Edm.DateTimeOffset`` — the only properties a supported filter
    may compare — mapped to that type.
    """
    properties: dict[str, str] = {}
    for name, field in model.model_fields.items():
        try:
            edm_type, _ = _edm_type(field.annotation)
        except TypeError:
            continue
        if edm_type in ("Edm.Date", "Edm.DateTimeOffset"):
            properties[name] = edm_type
    return properties


def _next_link(request: Request, next_skip: int) -> str:
    """The same URL on the same origin with ``$skip`` advanced.

    Carries the other options of the request forward (percent-encoded)
    except the ignored ``$format`` hint.
    """
    items = [
        (name, quote(value, safe=""))
        for name, value in request.query_params.multi_items()
        if name in _SUPPORTED_OPTIONS and name not in ("$skip", "$format")
    ]
    items.append(("$skip", str(next_skip)))
    query = "&".join(f"{name}={value}" for name, value in items)
    return f"{request.url.scheme}://{request.url.netloc}" f"{request.url.path}?{query}"


def _make_collection_endpoint(set_name: str, model: type[BaseModel]):
    """Build the ``GET /odata/<Set>`` endpoint for one ranked set."""
    declared = set(model.model_fields)
    filterable = _filterable_properties(model)
    key_properties = _set_key_properties(set_name)

    def collection(
        request: Request,
        top: str | None = Query(default=None, alias="$top"),
        skip: str | None = Query(default=None, alias="$skip"),
        format_: str | None = Query(default=None, alias="$format"),
        orderby: str | None = Query(default=None, alias="$orderby"),
        filter_: str | None = Query(default=None, alias="$filter"),
    ) -> JSONResponse:
        """The set's ranked rows as an OData v4 collection.

        Every row carries exactly the properties the set's EntityType
        in $metadata declares (list fields joined with ``"; "``),
        including the rows' ``as_of``.  ``$filter`` — one of ``<P> gt
        <L>``, ``<P> ge <L>``, ``<P> eq <L>`` or ``<P> gt <L> or <P>
        eq <L>`` with the same property both times, where ``<P>`` is a
        property the EntityType declares as ``Edm.Date`` or
        ``Edm.DateTimeOffset`` and ``<L>`` an ISO date or timestamp —
        keeps the matching rows before ordering and paging (an
        ``Edm.Date`` comparison uses the literal's date part; rows
        whose property is null never match) and ``@odata.nextLink``
        carries it forward.  ``$top`` (1 to 1000, default 1000) and
        ``$skip`` (0 or more, default 0) page the rows, which are
        ordered by the set's full key tuple (item_code, site_code —
        plus period for the Forecast set) unless ``$orderby`` lists
        declared properties with an optional ``asc``; while a page is
        not the last one the answer adds
        ``@odata.nextLink`` on the same origin.  ``$format=json`` is
        accepted and ignored; any other option, and any ``$filter``
        outside those shapes (notably ``$select`` and ``$expand``),
        answers 400 "unsupported query option", as do out-of-bounds
        ``$top``/``$skip`` values in the OData v4 error format.  The
        endpoint only reads: a ValueError from the ranked function
        answers 400 — in the OData v4 error format for the Changes
        set, as the ``{"detail": ...}`` body for the other sets.
        """
        for name in request.query_params:
            if name not in _SUPPORTED_OPTIONS:
                return _odata_error("unsupported query option")
        if orderby is not None:
            properties = _parse_orderby(orderby, declared)
            if properties is None:
                return _odata_error("unsupported query option")
        else:
            properties = None
        if filter_ is not None:
            clauses = _parse_filter(filter_, filterable)
            if clauses is None:
                return _odata_error("unsupported query option")
        else:
            clauses = None
        if top is not None:
            page_size = _parse_page_value(top, _TOP_MIN, _TOP_MAX)
            if page_size is None:
                return _odata_error(
                    "the $top query option must be an integer "
                    f"between {_TOP_MIN} and {_TOP_MAX}",
                    "$top",
                )
        else:
            page_size = _TOP_DEFAULT
        if skip is not None:
            page_skip = _parse_page_value(skip, _SKIP_MIN, None)
            if page_skip is None:
                return _odata_error(
                    "the $skip query option must be an integer "
                    f"of at least {_SKIP_MIN}",
                    "$skip",
                )
        else:
            page_skip = _SKIP_DEFAULT
        try:
            rows = _RANKED_FUNCTIONS[set_name]()
        except ValueError as exc:
            if set_name == "Changes":
                return _odata_error(str(exc))
            raise HTTPException(status_code=400, detail=str(exc))
        if clauses is not None:
            rows = _apply_filter(rows, clauses)
        ordered = _ordered_rows(rows, model, properties, key_properties)
        page = ordered[page_skip : page_skip + page_size]
        body: dict[str, Any] = {
            "@odata.context": f"$metadata#{set_name}",
            "value": page,
        }
        if page_skip + page_size < len(ordered):
            body["@odata.nextLink"] = _next_link(request, page_skip + page_size)
        return JSONResponse(content=body)

    collection.__name__ = f"odata_{set_name.lower()}_collection"
    return collection


for _set_name, _row_model in _SETS:
    router.add_api_route(
        f"/{_set_name}",
        _make_collection_endpoint(_set_name, _row_model),
        methods=["GET"],
        name=f"odata_{_set_name.lower()}_collection",
    )
