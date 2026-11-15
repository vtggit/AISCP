"""Acumatica -> AIERP currency sync.

``sync_acumatica_currencies`` stores the currencies Acumatica's
ledgers report in for the ``acumatica`` source.
"""

from app.feed.acumatica_client import AcumaticaClient, AcumaticaError
from app.repositories.erp_source_currencies_postgres_repository import (
    ErpSourceCurrencyPostgresRepository,
)

SOURCE_SYSTEM = "acumatica"


def sync_acumatica_currencies(reader=None, repo=None) -> list[str] | None:
    """Store the Acumatica ledger currencies for ``acumatica``.

    ``reader`` defaults to a new ``AcumaticaClient().read_ledger_currencies``
    and ``repo`` to an ``ErpSourceCurrencyPostgresRepository``. When the
    read answers, the codes are stored for ``acumatica`` through
    ``repo.replace_for("acumatica", codes)`` — exactly the codes the
    read answered, in one transaction — and the codes are answered.
    When the read raises ``AcumaticaError`` — a missing configuration
    included — nothing is stored, any stored ``acumatica`` rows are left
    exactly as they were, and the answer is ``None``.
    """
    if reader is None:
        reader = AcumaticaClient().read_ledger_currencies
    if repo is None:
        repo = ErpSourceCurrencyPostgresRepository()
    try:
        codes = reader()
    except AcumaticaError:
        return None
    repo.replace_for(SOURCE_SYSTEM, codes)
    return codes
