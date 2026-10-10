"""Stable CSV schemas shared by the generator, validator and future simulator."""

NODE_TYPES = (
    "supplier",
    "regional_warehouse",
    "city_warehouse",
    "terminal",
)
TIER_INDEX = {node_type: index for index, node_type in enumerate(NODE_TYPES)}
EDGE_TIERS = (
    "supplier_to_regional_warehouse",
    "regional_warehouse_to_city_warehouse",
    "city_warehouse_to_terminal",
)

NODE_FIELDS = (
    "node_id",
    "node_type",
    "tier",
    "upstream_node_id",
    "downstream_node_ids",
    "sku_id",
    "nominal_daily_flow_units",
    "initial_on_hand_units",
    "storage_capacity_units",
    "daily_processing_capacity_units",
    "daily_production_capacity_units",
)
INVENTORY_FIELDS = (
    "state_date",
    "node_id",
    "sku_id",
    "on_hand_units",
    "reserved_units",
    "backorder_units",
    "in_transit_inbound_units",
)
CALENDAR_FIELDS = (
    "date",
    "terminal_id",
    "sku_id",
    "weekday_monday_zero",
    "promotion_id",
    "planned_promotion_lift",
)
HISTORY_FIELDS = (
    "date",
    "terminal_id",
    "sku_id",
    "demand_units",
    "promotion_id",
    "planned_promotion_lift",
)
FUTURE_FIELDS = (
    "scenario_id",
    "date",
    "terminal_id",
    "sku_id",
    "demand_units",
)
