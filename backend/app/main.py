"""AIERP FastAPI application.

The decision layer over an existing ERP: AIERP reads ERP data (via the AIDW OData
substrate) and recommends. The ERP stays the system of record.

Router imports live INSIDE create_app() at 4-space indent, matching the house idiom the
sibling products use: the entity recipe anchors on the last `from app.api.X import
router` line and inserts its own at that indentation, so a module-level import block
produces an indentation error when a new entity registers itself.
"""

from fastapi import Depends, FastAPI, Request, Security
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer

from app.db.connection import PoolTimeoutError

APP_NAME = "AIERP"
APP_VERSION = "0.2.0"


def _pool_timeout_response(request: Request, exc: PoolTimeoutError) -> JSONResponse:
    """A request that waited out the pool's checkout deadline.

    The pool, not the request, is the bottleneck: the answer is a
    503 with a retry hint, so a caller that retries finds a free
    connection instead of reading a server error.
    """
    return JSONResponse(
        status_code=503,
        content={"detail": "The database is busy; try again shortly."},
        headers={"Retry-After": "5"},
    )


def create_app() -> FastAPI:
    """Build the FastAPI application and mount every router.

    Every router except the liveness probe and the auth router is
    mounted with ``require_authenticated_user`` applied once at router
    level, so the served app (``app`` below) answers 401 to
    unauthenticated /api traffic.  The OData feed is the first
    exception: it is mounted WITHOUT the user gate and authenticates
    with the HTTP Basic machine credential (``AIERP_ODATA_USER`` /
    ``AIERP_ODATA_PASSWORD``) instead.  The auth router is the second:
    it is mounted WITHOUT the user gate so that ``GET /api/auth/config``
    stays public (the frontend needs the IdP metadata before the caller
    has any token); ``GET /api/auth/me`` protects itself with
    ``require_authenticated_user``.  No parameter, flag or second app
    object can mount the API routers without the gate.
    """
    from app.api.abc import router as abc_router
    from app.api.aggregate import router as aggregate_router
    from app.api.auth import router as auth_router
    from app.api.cross_erp import router as cross_erp_router
    from app.api.ddmrp import router as ddmrp_router
    from app.api.decision_summary import router as decision_summary_router
    from app.api.demand_events import router as demand_events_router
    from app.api.demand_signals import router as demand_signals_router
    from app.api.demand_stats import router as demand_stats_router
    from app.api.drift import router as drift_router
    from app.api.empirical_adoptions import router as empirical_adoptions_router
    from app.api.erp_currencies import router as erp_currencies_router
    from app.api.erp_mappings import router as erp_mappings_router
    from app.api.erp_sources import router as erp_sources_router
    from app.api.erp_standing_grants import router as erp_standing_grants_router
    from app.api.erp_standing_grants import standing_grants_validation_guard
    from app.api.exceptions import router as exceptions_router
    from app.api.excess import router as excess_router
    from app.api.excess_decisions import router as excess_decisions_router
    from app.api.expedite import router as expedite_router
    from app.api.feed_status import router as feed_status_router
    from app.api.forecast import router as forecast_router
    from app.api.guidance import router as guidance_router
    from app.api.health import router as health_router
    from app.api.inventory_items import router as inventory_items_router
    from app.api.item_sites import router as item_sites_router
    from app.api.lead_time_observations import router as lead_time_observations_router
    from app.api.lead_time_stats import router as lead_time_stats_router
    from app.api.odata import router as odata_router
    from app.api.on_hand import router as on_hand_router
    from app.api.parameter_writeback import router as parameter_writeback_router
    from app.api.planner_decisions import router as planner_decisions_router
    from app.api.purchase_budgets import router as purchase_budgets_router
    from app.api.recommendation_changes import router as recommendation_changes_router
    from app.api.recommendations import router as recommendations_router
    from app.api.replenishment_actions import router as replenishment_actions_router
    from app.api.scenarios import router as scenarios_router
    from app.api.second_sources import router as second_sources_router
    from app.api.service_levels import router as service_levels_router
    from app.api.settings import router as settings_router
    from app.api.sop import router as sop_router
    from app.api.stockout import router as stockout_router
    from app.api.stockout_decisions import router as stockout_decisions_router
    from app.api.supplier_order_minimums import router as supplier_order_minimums_router
    from app.api.supplier_performance import router as supplier_performance_router
    from app.api.supplier_risk import router as supplier_risk_router
    from app.api.supplier_terms import router as supplier_terms_router
    from app.api.suppliers import router as suppliers_router
    from app.api.supply_plan import router as supply_plan_router
    from app.api.work import router as work_router
    from app.auth.dependencies import require_authenticated_user

    application = FastAPI(title=APP_NAME, version=APP_VERSION)
    # The standing-grants writes answer 401/403 before any body
    # validation: FastAPI decodes a JSON request body before it
    # resolves the route's dependencies, so a body that is not valid
    # JSON would otherwise answer 422 before the admin check runs.
    # The guard re-runs the admin check on the two write paths and
    # passes every other request to the default 422 handler.
    application.add_exception_handler(
        RequestValidationError, standing_grants_validation_guard
    )
    # A request that waited out the pool's checkout deadline on a
    # full pool: 503 with a retry hint, not a 500.
    application.add_exception_handler(PoolTimeoutError, _pool_timeout_response)
    # One router-level dependency gates every route below; the health
    # probe stays open for liveness checks.
    auth_gate = [
        Depends(require_authenticated_user),
        Security(HTTPBearer(auto_error=False, scheme_name="bearerAuth")),
    ]
    application.include_router(health_router)
    # The auth router is NOT mounted with the user gate: /api/auth/config
    # must stay public for the frontend, and /api/auth/me protects
    # itself with require_authenticated_user.
    application.include_router(auth_router)
    # The OData feed is NOT mounted with the user gate: its routes
    # authenticate with the machine Basic credential instead.
    application.include_router(odata_router)
    application.include_router(abc_router, dependencies=auth_gate)
    application.include_router(aggregate_router, dependencies=auth_gate)
    application.include_router(cross_erp_router, dependencies=auth_gate)
    application.include_router(inventory_items_router, dependencies=auth_gate)
    application.include_router(item_sites_router, dependencies=auth_gate)
    application.include_router(demand_events_router, dependencies=auth_gate)
    application.include_router(demand_signals_router, dependencies=auth_gate)
    application.include_router(demand_stats_router, dependencies=auth_gate)
    application.include_router(ddmrp_router, dependencies=auth_gate)
    application.include_router(decision_summary_router, dependencies=auth_gate)
    application.include_router(drift_router, dependencies=auth_gate)
    application.include_router(planner_decisions_router, dependencies=auth_gate)
    application.include_router(purchase_budgets_router, dependencies=auth_gate)
    application.include_router(excess_router, dependencies=auth_gate)
    application.include_router(excess_decisions_router, dependencies=auth_gate)
    application.include_router(empirical_adoptions_router, dependencies=auth_gate)
    application.include_router(expedite_router, dependencies=auth_gate)
    application.include_router(erp_currencies_router, dependencies=auth_gate)
    application.include_router(erp_mappings_router, dependencies=auth_gate)
    application.include_router(erp_sources_router, dependencies=auth_gate)
    application.include_router(erp_standing_grants_router, dependencies=auth_gate)
    application.include_router(exceptions_router, dependencies=auth_gate)
    application.include_router(feed_status_router, dependencies=auth_gate)
    application.include_router(guidance_router, dependencies=auth_gate)
    application.include_router(forecast_router, dependencies=auth_gate)
    application.include_router(lead_time_observations_router, dependencies=auth_gate)
    application.include_router(lead_time_stats_router, dependencies=auth_gate)
    application.include_router(on_hand_router, dependencies=auth_gate)
    application.include_router(parameter_writeback_router, dependencies=auth_gate)
    application.include_router(recommendation_changes_router, dependencies=auth_gate)
    application.include_router(recommendations_router, dependencies=auth_gate)
    application.include_router(replenishment_actions_router, dependencies=auth_gate)
    application.include_router(scenarios_router, dependencies=auth_gate)
    application.include_router(second_sources_router, dependencies=auth_gate)
    application.include_router(service_levels_router, dependencies=auth_gate)
    application.include_router(settings_router, dependencies=auth_gate)
    application.include_router(sop_router, dependencies=auth_gate)
    application.include_router(stockout_router, dependencies=auth_gate)
    application.include_router(stockout_decisions_router, dependencies=auth_gate)
    application.include_router(supplier_order_minimums_router, dependencies=auth_gate)
    application.include_router(supplier_performance_router, dependencies=auth_gate)
    application.include_router(supplier_risk_router, dependencies=auth_gate)
    application.include_router(supplier_terms_router, dependencies=auth_gate)
    application.include_router(suppliers_router, dependencies=auth_gate)
    application.include_router(supply_plan_router, dependencies=auth_gate)
    application.include_router(work_router, dependencies=auth_gate)
    return application


app = create_app()
