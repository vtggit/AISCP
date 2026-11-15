"""Proving test for issue #54: as_of query parameter on /api/recommendations/by-key."""

from datetime import date
from unittest.mock import patch

from app.api import recommendations as rec_module


def test_issue54_surgical(client):
    """The /by-key endpoint accepts as_of and passes it to recommendation_for_key."""
    mock_row = {
        "item_code": "ITM54",
        "site_code": "SITE54",
        "reorder_point": 100.0,
        "safety_stock": 20.0,
    }

    def make_response(as_of):
        return {
            "item_code": "ITM54",
            "site_code": "SITE54",
            "as_of": as_of,
            "status": "ok",
            "demand_class": "smooth",
            "method": "service_level",
            "notes": [],
            "current": {"reorder_point": 100.0, "safety_stock": 20.0},
            "inputs": {
                "d": 1.0 if as_of == date(2026, 2, 1) else 2.5,
                "sd_d": 0.5,
                "lt": 7.0,
                "sd_lt": 1.0,
                "z": 1.645,
                "service_level": 0.95,
            },
            "reorder_point": 50.0,
            "safety_stock": 10.0,
            "arithmetic": "test",
        }

    def mock_recommendation_for_key(
        item_code, site_code, as_of=None, item_site_row=None
    ):
        if as_of is None:
            as_of = date(2026, 3, 20)
        return make_response(as_of)

    with patch.object(rec_module, "_repo") as mock_repo:
        mock_repo.get_by_key.return_value = mock_row
        with patch.object(
            rec_module,
            "recommendation_for_key",
            side_effect=mock_recommendation_for_key,
        ) as mock_fn:
            # Call without as_of (uses latest transaction date as as-of)
            resp_default = client.get(
                "/api/recommendations/by-key",
                params={"item_code": "ITM54", "site_code": "SITE54"},
            )
            assert resp_default.status_code == 200
            data_default = resp_default.json()
            assert data_default["as_of"] == "2026-03-20"

            # Call with as_of before the later events
            resp_asof = client.get(
                "/api/recommendations/by-key",
                params={
                    "item_code": "ITM54",
                    "site_code": "SITE54",
                    "as_of": "2026-02-01",
                },
            )
            assert resp_asof.status_code == 200
            data_asof = resp_asof.json()
            assert data_asof["as_of"] == "2026-02-01"

            # Responses differ in as_of and demand inputs
            assert data_asof["as_of"] != data_default["as_of"]
            assert data_asof["inputs"]["d"] != data_default["inputs"]["d"]

            # as_of was passed positionally to recommendation_for_key
            assert mock_fn.call_args_list[1].args[2] == date(2026, 2, 1)
            assert mock_fn.call_args_list[0].args[2] is None

            # ValueError still returns 400
            def raise_value_error(*a, **kw):
                raise ValueError("bad input")

            mock_fn.side_effect = raise_value_error
            resp_err = client.get(
                "/api/recommendations/by-key",
                params={"item_code": "ITM54", "site_code": "SITE54"},
            )
            assert resp_err.status_code == 400
