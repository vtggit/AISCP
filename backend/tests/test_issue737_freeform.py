"""Issue #737 proof: the confirm route calls the Acumatica writer plainly.

The issue 716 proof proved that no route module called
``write_item_warehouse_parameters`` back then, so the route module kept
the name out of its source text (a ``_ACUMATICA_WRITE_METHOD`` constant
resolved with ``getattr``).  The issue 719 confirm route became the
method's first caller and kept that indirection; this issue removes it:
``app/api/parameter_writeback.py`` now names the method in plain source.

Proved here, with no network and no ERP call:

  * the route module's source contains the plain expression
    ``AcumaticaClient().write_item_warehouse_parameters`` and contains
    neither the ``_ACUMATICA_WRITE_METHOD`` indirection constant nor
    any ``getattr(`` call;
  * ``_writer_for("acumatica")`` returns a bound method named
    ``write_item_warehouse_parameters`` of a fresh ``AcumaticaClient``;
  * ``_writer_for("odoo")`` returns a bound method named
    ``write_reorder_rule`` of a fresh ``OdooClient``;
  * neither writer is called — both client classes read their
    environment only at call time, so returning the bound methods
    touches no configuration, no transport and no ERP.
"""

import pathlib
import types

import app.api.parameter_writeback as parameter_writeback
from app.feed.acumatica_client import AcumaticaClient
from app.feed.odoo_client import OdooClient


def test_issue737_freeform() -> None:
    # -- 1. the source: the plain call, no indirection ---------------------
    source = pathlib.Path(parameter_writeback.__file__).read_text()
    assert "AcumaticaClient().write_item_warehouse_parameters" in source
    assert "_ACUMATICA_WRITE_METHOD" not in source
    assert "getattr(" not in source

    # -- 2. the writers: the bound methods, never called -------------------
    acumatica_writer = parameter_writeback._writer_for("acumatica")
    assert isinstance(acumatica_writer, types.MethodType)
    assert acumatica_writer.__name__ == "write_item_warehouse_parameters"
    assert isinstance(acumatica_writer.__self__, AcumaticaClient)
    expected = AcumaticaClient.write_item_warehouse_parameters
    assert acumatica_writer.__func__ is expected

    odoo_writer = parameter_writeback._writer_for("odoo")
    assert isinstance(odoo_writer, types.MethodType)
    assert odoo_writer.__name__ == "write_reorder_rule"
    assert isinstance(odoo_writer.__self__, OdooClient)
    expected = OdooClient.write_reorder_rule
    assert odoo_writer.__func__ is expected

    # Neither returned writer is called: no request, no audit row, no
    # item-site update — the bound methods above are the whole proof,
    # and both client classes read their environment only at call time.
