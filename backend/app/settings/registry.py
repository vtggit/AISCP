"""The settings registry: every storable setting is declared exactly once.

A setting declares its key, its scope (``system`` or ``user``), its type
(``boolean``, ``enum`` with its allowed values, or ``number`` with its
``min`` and ``max`` bounds), its default and a one-sentence description.
Only registered keys can be stored: the API answers 404 for any other
key, and the database is only ever written through the API.
"""

from dataclasses import dataclass
from typing import Any


def _is_number(value: object) -> bool:
    """A real number: an int or float, never a bool."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


@dataclass(frozen=True)
class Setting:
    """One registered setting."""

    key: str
    scope: str  # "system" or "user"
    type: str  # "boolean", "enum" or "number"
    default: Any
    description: str
    allowed: tuple[str, ...] | None = None  # enum values; None otherwise
    min: float | None = None  # number bounds; None for a boolean or enum
    max: float | None = None  # number bounds; None for a boolean or enum

    def __post_init__(self) -> None:
        if self.scope not in ("system", "user"):
            raise ValueError(f"setting {self.key!r}: scope must be 'system' or 'user'")
        if self.type not in ("boolean", "enum", "number"):
            raise ValueError(
                f"setting {self.key!r}: type must be 'boolean', 'enum' or 'number'"
            )
        if self.type == "enum":
            if not self.allowed:
                raise ValueError(
                    f"setting {self.key!r}: an enum setting needs allowed values"
                )
            if self.default not in self.allowed:
                raise ValueError(
                    f"setting {self.key!r}: default must be one of the allowed values"
                )
        elif self.type == "number":
            if self.allowed is not None:
                raise ValueError(
                    f"setting {self.key!r}: a number setting has no allowed values"
                )
            if not _is_number(self.min) or not _is_number(self.max):
                raise ValueError(
                    f"setting {self.key!r}: a number setting needs a min and a max"
                )
            if not _is_number(self.default):
                raise ValueError(
                    f"setting {self.key!r}: a number setting needs a numeric default"
                )
            if not (self.min <= self.default <= self.max):
                raise ValueError(
                    f"setting {self.key!r}: the default must be between min and max"
                )
        elif self.allowed is not None:
            raise ValueError(
                f"setting {self.key!r}: a boolean setting has no allowed values"
            )
        if self.type in ("boolean", "enum") and (
            self.min is not None or self.max is not None
        ):
            raise ValueError(
                f"setting {self.key!r}: min and max belong to a number setting"
            )


_REGISTRY: dict[str, Setting] = {}


def register(setting: Setting) -> None:
    """Declare one setting.  Registering a key twice is a programming error."""
    if setting.key in _REGISTRY:
        raise ValueError(f"setting {setting.key!r} is already registered")
    _REGISTRY[setting.key] = setting


def get_setting(key: str) -> Setting | None:
    """The registered setting with *key*, or None when the key is unknown."""
    return _REGISTRY.get(key)


def all_settings() -> list[Setting]:
    """Every registered setting, ordered by key."""
    return [_REGISTRY[key] for key in sorted(_REGISTRY)]


# ---------------------------------------------------------------------------
# The registered settings
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# The registered settings
# ---------------------------------------------------------------------------

register(
    Setting(
        key="ddmrp.adu_window_days",
        scope="system",
        type="number",
        default=90,
        description="The demand window, in days, over which a DDMRP item-site's average daily usage (ADU) is computed.",
        min=7,
        max=365,
    )
)

register(
    Setting(
        key="ddmrp.lead_time_factor_short",
        scope="system",
        type="number",
        default=0.8,
        description="The lead-time fraction the DDMRP red base is built on for short lead times (under 15 days).",
        min=0.01,
        max=1,
    )
)

register(
    Setting(
        key="ddmrp.lead_time_factor_medium",
        scope="system",
        type="number",
        default=0.5,
        description="The lead-time fraction the DDMRP red base is built on for medium lead times (15 to 45 days).",
        min=0.01,
        max=1,
    )
)

register(
    Setting(
        key="ddmrp.lead_time_factor_long",
        scope="system",
        type="number",
        default=0.3,
        description="The lead-time fraction the DDMRP red base is built on for long lead times (over 45 days).",
        min=0.01,
        max=1,
    )
)

register(
    Setting(
        key="ddmrp.variability_factor_low",
        scope="system",
        type="number",
        default=0.25,
        description="The demand-variability fraction the DDMRP red safety stock is scaled by for low-variability demand.",
        min=0,
        max=1.5,
    )
)

register(
    Setting(
        key="ddmrp.variability_factor_medium",
        scope="system",
        type="number",
        default=0.5,
        description="The demand-variability fraction the DDMRP red safety stock is scaled by for medium-variability demand.",
        min=0,
        max=1.5,
    )
)

register(
    Setting(
        key="ddmrp.variability_factor_high",
        scope="system",
        type="number",
        default=0.75,
        description="The demand-variability fraction the DDMRP red safety stock is scaled by for high-variability demand.",
        min=0,
        max=1.5,
    )
)

register(
    Setting(
        key="exceptions.forecast_bias_min_periods",
        scope="system",
        type="number",
        default=3,
        description="The fewest compared forecast periods an item-site needs before its bias can appear as an exception.",
        min=1,
        max=120,
    )
)

register(
    Setting(
        key="exceptions.forecast_bias_periods",
        scope="system",
        type="number",
        default=6,
        description="How many of the latest forecast periods, each forecast one period ahead, the forecast bias is measured over.",
        min=1,
        max=36,
    )
)

register(
    Setting(
        key="exceptions.forecast_bias_threshold",
        scope="system",
        type="number",
        default=0.5,
        description="The absolute forecast bias (0.5 = 50%) at or above which an item-site appears in the exceptions inbox as forecast bias.",
        min=0.1,
        max=10,
    )
)

register(
    Setting(
        key="exceptions.order_minimum_due_days",
        scope="system",
        type="number",
        default=2,
        description="Days within which a line of an order below its supplier's minimum must be ordered for the order to appear in the exceptions inbox.",
        min=0,
        max=7,
    )
)

register(
    Setting(
        key="exceptions.release_approval_days",
        scope="system",
        type="number",
        default=3,
        description="Days a released order may wait for approval in the ERP before it appears in the exceptions inbox.",
        min=1,
        max=60,
    )
)

register(
    Setting(
        key="exceptions.sop_signoff_day",
        scope="system",
        type="number",
        default=10,
        description="Day of the month after which a month with no S&OP sign-off appears in the exceptions inbox.",
        min=1,
        max=28,
    )
)

register(
    Setting(
        key="erp.reporting_currency",
        scope="system",
        type="enum",
        default="USD",
        description="The currency every ERP is assumed to report in.",
        allowed=("USD", "EUR", "GBP", "CAD", "AUD", "JPY", "CHF", "MXN"),
    )
)

register(
    Setting(
        key="erp.standing_grants_enabled",
        scope="system",
        type="boolean",
        default=True,
        description="When off, no standing grant writes to the ERP; every write then needs a person's approval of that write.",
    )
)

register(
    Setting(
        key="erp.standing_grants_horizon_days",
        scope="system",
        type="number",
        default=1,
        description="How many days ahead a standing-grant run of the order plan looks for due lines; 1 sends each line on the day it is due.",
        min=1,
        max=60,
    )
)

register(
    Setting(
        key="decisions.remedy_order",
        scope="system",
        type="enum",
        default="expedite,transfer,raise_order",
        description="The order in which a stockout row's remedy is looked for: the first kind that has a candidate is proposed.",
        allowed=(
            "expedite,transfer,raise_order",
            "expedite,raise_order,transfer",
            "transfer,expedite,raise_order",
            "transfer,raise_order,expedite",
            "raise_order,expedite,transfer",
            "raise_order,transfer,expedite",
        ),
    )
)

register(
    Setting(
        key="decisions.require_action_choice",
        scope="system",
        type="boolean",
        default=True,
        description="Accept on an excess or stockout row stays disabled until an action is chosen.",
    )
)

register(
    Setting(
        key="queue.default_filter",
        scope="user",
        type="enum",
        default="open",
        description="The filter the Work queue opens with.",
        allowed=("open", "decided", "all"),
    )
)

register(
    Setting(
        key="replenishment.demand_basis",
        scope="system",
        type="enum",
        default="history",
        description="The demand basis the reorder point is built on: observed history or the forecast.",
        allowed=("history", "forecast"),
    )
)

register(
    Setting(
        key="replenishment.intermittent_recommendations",
        scope="system",
        type="boolean",
        default=False,
        description="Recommend intermittent and lumpy item-sites on their empirical lead-time demand (off: shown for review only).",
    )
)

register(
    Setting(
        key="replenishment.lead_time_basis",
        scope="system",
        type="enum",
        default="item",
        description="Which receipts set the lead time behind safety stock: every supplier's for the item (item), or only the supplier the item is received from most (main_supplier).",
        allowed=("item", "main_supplier"),
    )
)

register(
    Setting(
        key="replenishment.service_level",
        scope="system",
        type="number",
        default=0.95,
        description="The cycle service level reorder points are computed at.",
        min=0.5,
        max=0.9999,
    )
)

register(
    Setting(
        key="replenishment.service_level_a",
        scope="system",
        type="number",
        default=0.98,
        description="The service level for class A item-sites (the items that make up the first 80% of annual consumption value).",
        min=0.5,
        max=0.9999,
    )
)

register(
    Setting(
        key="replenishment.service_level_b",
        scope="system",
        type="number",
        default=0.95,
        description="The service level for class B item-sites.",
        min=0.5,
        max=0.9999,
    )
)

register(
    Setting(
        key="replenishment.service_level_by_class",
        scope="system",
        type="boolean",
        default=False,
        description="Use a service level per ABC class instead of the single service level.",
    )
)

register(
    Setting(
        key="replenishment.service_level_c",
        scope="system",
        type="number",
        default=0.90,
        description="The service level for class C item-sites.",
        min=0.5,
        max=0.9999,
    )
)

register(
    Setting(
        key="replenishment.service_target",
        scope="system",
        type="enum",
        default="cycle",
        description="What the service levels mean: the chance of not running out in a cycle (cycle), or the share of demand served from stock (fill_rate).",
        allowed=("cycle", "fill_rate"),
    )
)

register(
    Setting(
        key="replenishment.order_cost",
        scope="system",
        type="number",
        default=50.0,
        description="The cost of placing one purchase order, used for the recommended order quantity.",
        min=0.01,
        max=100000,
    )
)

register(
    Setting(
        key="replenishment.holding_rate",
        scope="system",
        type="number",
        default=0.25,
        description="The yearly cost of holding stock, as a fraction of its unit cost.",
        min=0.01,
        max=1,
    )
)

register(
    Setting(
        key="second_source.qualification_days",
        scope="system",
        type="number",
        default=60,
        description="Days to qualify a new supplier and receive a first delivery, used for the bridge stock of an item with no supplier on terms to switch to.",
        min=1,
        max=365,
    )
)

register(
    Setting(
        key="second_source.similar_prefix_length",
        scope="system",
        type="number",
        default=3,
        description="How many leading characters of an item code make two items similar when suggesting a second source. 0 turns similar-item suggestions off.",
        min=0,
        max=10,
    )
)

register(
    Setting(
        key="supplier_risk.on_time_floor",
        scope="system",
        type="number",
        default=0.85,
        description="Below this on-time rate a supplier counts as late.",
        min=0,
        max=1,
    )
)

register(
    Setting(
        key="supplier_risk.lead_time_cv_ceiling",
        scope="system",
        type="number",
        default=0.6,
        description="Above this lead-time variation (standard deviation over mean) a supplier counts as variable.",
        min=0,
        max=5,
    )
)

register(
    Setting(
        key="supply_plan.prefer_lower_risk_supplier",
        scope="system",
        type="boolean",
        default=True,
        description="When an item has terms from more than one supplier, buy from the lower-risk one rather than the default.",
    )
)

register(
    Setting(
        key="supply_plan.top_up_lookahead_days",
        scope="system",
        type="number",
        default=30,
        description="How many days ahead the order plan looks for items to top up an order that is below its supplier's minimum.",
        min=1,
        max=90,
    )
)
