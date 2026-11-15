"""The currencies the ERP sources report in, and the reporting currency.

``GET /api/erp-currencies`` answers — with one read of
``erp_source_currencies`` and one read of the stored system settings,
and no writes — the ``erp.reporting_currency`` system setting
(resolved exactly as ``GET /api/exceptions`` resolves it: the stored
value when it is one of the allowed currencies, ignoring case, else
the registry default) and one entry per stored row of
``erp_source_currencies`` — ``source_system``, ``currency_code`` and
``seen_at`` (ISO-8601) — ordered by source system then code.  It is
open to any signed-in user, mounted behind the app's authentication
gate the same way as the cross-ERP router, and answers 401 without a
valid Bearer token.
"""

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.erp_mappings import require_any_authenticated_user
from app.api.exceptions import _reporting_currency, _stored_system_settings
from app.auth.models import AuthUser
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)


class ErpSourceCurrency(BaseModel):
    """One stored row of ``erp_source_currencies``.

    ``source_system`` the source system's code, ``currency_code`` the
    uppercase ISO-4217 code it reports in and ``seen_at`` the newest
    sync that saw the code (ISO-8601).
    """

    source_system: str
    currency_code: str
    seen_at: str


class ErpCurrenciesResponse(BaseModel):
    """The answer of ``GET /api/erp-currencies``.

    ``reporting_currency`` is the ``erp.reporting_currency`` system
    setting (the stored value when it is one of the allowed currencies,
    ignoring case, else the registry default) and ``sources`` one entry
    per stored row of ``erp_source_currencies``, ordered by source
    system then code — empty while no row is stored.
    """

    reporting_currency: str
    sources: list[ErpSourceCurrency]


router = APIRouter(prefix="/api/erp-currencies", tags=["erp-currencies"])

_repo = ErpSourceCurrencyPostgresRepository()


@router.get("", response_model=ErpCurrenciesResponse)
def list_erp_currencies(
    current_user: AuthUser = Depends(require_any_authenticated_user),
) -> dict:
    """The reporting currency, and the currencies the sources report in.

    ``reporting_currency`` is the ``erp.reporting_currency`` system
    setting, resolved exactly as ``GET /api/exceptions`` resolves it
    (the stored value when it is one of the allowed currencies,
    ignoring case, else the registry default), and ``sources`` one
    entry per stored row of ``erp_source_currencies`` —
    ``source_system``, ``currency_code`` and ``seen_at`` (ISO-8601) —
    ordered by source system then code, empty while no row is stored.
    One read of the stored rows and one read of the stored system
    settings — no writes — feed the answer; a stored row whose fields
    are not strings or whose ``seen_at`` is not a timestamp
    contributes nothing — nothing here can raise on a hostile row
    (never a 500).
    """
    reporting_currency = _reporting_currency(_stored_system_settings())
    sources = [
        {
            "source_system": row["source_system"],
            "currency_code": row["currency_code"],
            "seen_at": row["seen_at"].isoformat(),
        }
        for row in _repo.list_all()
        if isinstance(row.get("source_system"), str)
        and isinstance(row.get("currency_code"), str)
        and isinstance(row.get("seen_at"), datetime)
    ]
    return {"reporting_currency": reporting_currency, "sources": sources}
