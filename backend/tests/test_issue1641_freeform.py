"""Issue #1641 proof: one shared environment-switch parser.

``env_switch`` (``app.feed.switches``) is the single parser behind all
four switch variables — ``ODOO_ALLOW_WRITES``, ``ODOO_ALLOW_INSECURE``,
``ACUMATICA_ALLOW_WRITES``, and ``ACUMATICA_ALLOW_INSECURE``. For each
of them, ``1``, ``true``, ``TRUE``, `` yes ``, and ``On`` turn the
switch on, while unset, ``""``, ``" "``, ``0``, ``false``, ``no``,
``off``, and ``enabled`` leave it off — shown through ``env_switch``
itself and through each client's own functions (the Odoo client's
``_writes_enabled``/``_allow_insecure``, the Acumatica client's
``_is_truthy_env``/``_writes_enabled``/``_allow_insecure``). With
``ODOO_ALLOW_WRITES`` set to ``on``, ``OdooClient``'s
``create_draft_purchase_order`` gets past the switch and raises
``ValueError`` for an empty item code before any network call; with
the switch unset it raises ``OdooWritesDisabledError``. And a huge
integer ERP number that overflows the float conversion stays the
client's explicit error instead of an overflow.
"""

import pytest

from app.feed import acumatica_client, odoo_client
from app.feed.odoo_client import OdooClient, OdooWritesDisabledError
from app.feed.switches import TRUE_VALUES, env_switch

SWITCH_VARS = (
    "ODOO_ALLOW_WRITES",
    "ODOO_ALLOW_INSECURE",
    "ACUMATICA_ALLOW_WRITES",
    "ACUMATICA_ALLOW_INSECURE",
)
ON_VALUES = ("1", "true", "TRUE", " yes ", "On")
OFF_VALUES = ("", " ", "0", "false", "no", "off", "enabled")


def _client_checks(name: str):
    """Return the client's own zero-argument switch readers for ``name``."""
    if name == "ODOO_ALLOW_WRITES":
        return (odoo_client._writes_enabled,)
    if name == "ODOO_ALLOW_INSECURE":
        return (odoo_client._allow_insecure,)
    if name == "ACUMATICA_ALLOW_WRITES":
        client_checks = (acumatica_client._writes_enabled,)
    else:
        client_checks = (acumatica_client._allow_insecure,)
    return client_checks + (lambda: acumatica_client._is_truthy_env(name),)


def test_issue1641_freeform(monkeypatch):
    # The Acumatica client keeps its historical name for the shared
    # truthy tuple.
    assert acumatica_client._TRUTHY_VALUES is TRUE_VALUES

    for name in SWITCH_VARS:
        checks = _client_checks(name)
        for value in ON_VALUES:
            monkeypatch.setenv(name, value)
            assert env_switch(name) is True, (name, value)
            for check in checks:
                assert check() is True, (name, value, check)
        for value in (None, *OFF_VALUES):
            if value is None:
                monkeypatch.delenv(name, raising=False)
            else:
                monkeypatch.setenv(name, value)
            assert env_switch(name) is False, (name, value)
            for check in checks:
                assert check() is False, (name, value, check)

    # -- the write switch gates create_draft_purchase_order. -------------
    # No Odoo configuration is set at all: a call that got past the
    # switch and past argument validation would fail on the missing
    # configuration, so a ValueError proves the empty item code is
    # rejected before any configuration read or network call.
    for var in (
        "ODOO_BASE_URL",
        "ODOO_DB",
        "ODOO_LOGIN",
        "ODOO_API_KEY",
        "ODOO_CA_BUNDLE",
        "ODOO_TIMEOUT_SECONDS",
        "ODOO_ALLOW_INSECURE",
        "ODOO_ALLOW_WRITES",
    ):
        monkeypatch.delenv(var, raising=False)

    client = OdooClient()

    with pytest.raises(OdooWritesDisabledError):
        client.create_draft_purchase_order(
            item_code="", site_code="S", supplier_code="V", quantity=1, unit_price=1
        )

    monkeypatch.setenv("ODOO_ALLOW_WRITES", "on")
    with pytest.raises(ValueError, match="item_code must be a non-empty string"):
        client.create_draft_purchase_order(
            item_code="", site_code="S", supplier_code="V", quantity=1, unit_price=1
        )

    # -- a huge integer ERP field stays the client error. --------------
    # An int beyond the float range used to escape as an OverflowError;
    # the parser must answer the explicit malformed-field error, and a
    # large-but-fitting int still parses.
    with pytest.raises(acumatica_client.AcumaticaError, match="malformed field"):
        acumatica_client._po_number(
            {"OrderQty": {"value": 10**400}},
            "OrderQty",
            "the purchase order",
            default=None,
        )
    assert (
        acumatica_client._po_number(
            {"OrderQty": {"value": 10**15}},
            "OrderQty",
            "the purchase order",
            default=None,
        )
        == 1e15
    )
