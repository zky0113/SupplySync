"""Build exogenous network, starting state, calendar and consumer demand.

No policy-derived order, shipment, daily inventory, shortage or cost is created here.
"""

from __future__ import annotations

import hashlib
import math
import random
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from . import SCHEMA_VERSION
from .io_utils import csv_bytes, json_bytes
from .schema import (
    CALENDAR_FIELDS,
    FUTURE_FIELDS,
    HISTORY_FIELDS,
    INVENTORY_FIELDS,
    NODE_FIELDS,
    TIER_INDEX,
)


def _stream_seed(master_seed: int, label: str) -> int:
    digest = hashlib.sha256(f"{master_seed}|{label}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


def _round_units(value: float) -> int:
    return max(0, math.floor(value + 0.5))


def _date_sequence(start: date, count: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(count)]


def _build_network(config: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    node_specs = {item["id"]: item for item in config["network"]["nodes"]}
    children: dict[str, list[str]] = defaultdict(list)
    for item in node_specs.values():
        if item["upstream_id"] is not None:
            children[item["upstream_id"]].append(item["id"])

    def nominal_demand(node_id: str) -> int:
        item = node_specs[node_id]
        if item["type"] == "terminal":
            return item["base_daily_demand_units"]
        return sum(nominal_demand(child_id) for child_id in children[node_id])

    first_future = date.fromisoformat(config["history"]["start_date"]) + timedelta(days=config["history"]["days"])
    state_date = (first_future - timedelta(days=1)).isoformat()
    nodes: list[dict[str, Any]] = []
    inventory: list[dict[str, Any]] = []
    for node_id in sorted(node_specs, key=lambda item: (TIER_INDEX[node_specs[item]["type"]], item)):
        item = node_specs[node_id]
        node_type = item["type"]
        params = config["network"]["tier_parameters"][node_type]
        daily_flow = nominal_demand(node_id)
        capacity = math.ceil(daily_flow * params["capacity_cover_days"])
        handling = math.ceil(daily_flow * params["handling_capacity_multiplier"])
        production = (
            math.ceil(daily_flow * params["production_capacity_multiplier"])
            if node_type == "supplier"
            else 0
        )
        stock_rng = random.Random(_stream_seed(config["seed"], f"initial_stock:{node_id}"))
        stock = min(capacity, _round_units(daily_flow * params["initial_cover_days"] * stock_rng.uniform(0.95, 1.05)))
        nodes.append(
            {
                "node_id": node_id,
                "node_type": node_type,
                "tier": TIER_INDEX[node_type],
                "upstream_node_id": item["upstream_id"] or "",
                "downstream_node_ids": "|".join(sorted(children[node_id])),
                "sku_id": config["sku_id"],
                "nominal_daily_flow_units": daily_flow,
                "initial_on_hand_units": stock,
                "storage_capacity_units": capacity,
                "daily_processing_capacity_units": handling,
                "daily_production_capacity_units": production,
            }
        )
        inventory.append(
            {
                "state_date": state_date,
                "node_id": node_id,
                "sku_id": config["sku_id"],
                "on_hand_units": stock,
                "reserved_units": 0,
                "backorder_units": 0,
                "in_transit_inbound_units": 0,
            }
        )

    node_rows = {row["node_id"]: row for row in nodes}
    edges: list[dict[str, Any]] = []
    for item in sorted(node_specs.values(), key=lambda row: (row["upstream_id"] or "", row["id"])):
        if item["upstream_id"] is None:
            continue
        upstream = node_specs[item["upstream_id"]]
        tier = f"{upstream['type']}_to_{item['type']}"
        params = config["network"]["transport_by_tier"][tier]
        edges.append(
            {
                "from_node": upstream["id"],
                "to_node": item["id"],
                "edge_tier": tier,
                "base_lead_time_days": params["lead_time_days"],
                "transport_capacity_units_per_day": math.ceil(node_rows[item["id"]]["nominal_daily_flow_units"] * params["capacity_multiplier"]),
                "minimum_order_units": params["minimum_order_units"],
                "order_multiple_units": params["order_multiple_units"],
            }
        )
    return nodes, inventory, edges


def _promotion_for(config: dict[str, Any], day: date, terminal_id: str) -> tuple[str, float]:
    for promotion in config["demand"]["promotions"]:
        targets = promotion["terminal_ids"]
        if (
            date.fromisoformat(promotion["start_date"]) <= day <= date.fromisoformat(promotion["end_date"])
            and (targets == "all" or terminal_id in targets)
        ):
            return promotion["id"], promotion["lift"]
    return "", 0.0


def _poisson_chunk(mean: float, rng: random.Random) -> int:
    if mean <= 0:
        return 0
    threshold = math.exp(-mean)
    product = 1.0
    count = 0
    while product > threshold:
        product *= rng.random()
        count += 1
    return count - 1


def _poisson(mean: float, rng: random.Random) -> int:
    # Sum of independent Poisson chunks is exactly Poisson(mean), without
    # underflow for larger business demand levels.
    total = 0
    while mean > 50.0:
        total += _poisson_chunk(50.0, rng)
        mean -= 50.0
    return total + _poisson_chunk(mean, rng)


def _ar_step(previous: float, spec: dict[str, float], rng: random.Random) -> float:
    phi = spec["ar_phi"]
    sigma = spec["stationary_sigma"]
    return phi * previous + sigma * math.sqrt(1.0 - phi * phi) * rng.gauss(0.0, 1.0)


def _build_demand(config: dict[str, Any], nodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    raw_nodes = {item["id"]: item for item in config["network"]["nodes"]}
    terminals = sorted(item["id"] for item in raw_nodes.values() if item["type"] == "terminal")
    regions: dict[str, str] = {}
    for terminal_id in terminals:
        ancestor = raw_nodes[terminal_id]
        while ancestor["type"] != "regional_warehouse":
            ancestor = raw_nodes[ancestor["upstream_id"]]
        regions[terminal_id] = ancestor["id"]

    demand_cfg = config["demand"]
    master_seed = config["seed"]
    global_rng = random.Random(_stream_seed(master_seed, "global_shock"))
    region_rngs = {region_id: random.Random(_stream_seed(master_seed, f"region_shock:{region_id}")) for region_id in sorted(set(regions.values()))}
    terminal_rngs = {terminal_id: random.Random(_stream_seed(master_seed, f"terminal_shock:{terminal_id}")) for terminal_id in terminals}
    count_rngs = {terminal_id: random.Random(_stream_seed(master_seed, f"poisson:{terminal_id}")) for terminal_id in terminals}
    global_state = 0.0
    region_states = {region_id: 0.0 for region_id in region_rngs}
    terminal_states = {terminal_id: 0.0 for terminal_id in terminals}
    shock_variance = sum(demand_cfg[name]["stationary_sigma"] ** 2 for name in ("global_shock", "regional_shock", "terminal_shock"))

    start = date.fromisoformat(config["history"]["start_date"])
    history_days = config["history"]["days"]
    horizon = config["simulation"]["horizon_days"]
    dates = _date_sequence(start, history_days + horizon)
    calendar: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    baseline_future: dict[tuple[str, str], int] = {}

    for day_index, day in enumerate(dates):
        global_state = _ar_step(global_state, demand_cfg["global_shock"], global_rng)
        for region_id in region_rngs:
            region_states[region_id] = _ar_step(region_states[region_id], demand_cfg["regional_shock"], region_rngs[region_id])
        for terminal_id in terminals:
            terminal_states[terminal_id] = _ar_step(terminal_states[terminal_id], demand_cfg["terminal_shock"], terminal_rngs[terminal_id])
            promo_id, promo_lift = _promotion_for(config, day, terminal_id)
            day_text = day.isoformat()
            calendar.append(
                {
                    "date": day_text,
                    "terminal_id": terminal_id,
                    "sku_id": config["sku_id"],
                    "weekday_monday_zero": day.weekday(),
                    "promotion_id": promo_id,
                    "planned_promotion_lift": promo_lift,
                }
            )
            trend = (1.0 + demand_cfg["daily_trend_rate"]) ** day_index
            week = demand_cfg["weekday_multipliers_monday_first"][day.weekday()]
            year = 1.0 + demand_cfg["annual_amplitude"] * math.cos(
                2.0 * math.pi * (day.timetuple().tm_yday - demand_cfg["annual_peak_day_of_year"]) / 365.25
            )
            correlated_noise = math.exp(
                global_state + region_states[regions[terminal_id]] + terminal_states[terminal_id] - 0.5 * shock_variance
            )
            mean = raw_nodes[terminal_id]["base_daily_demand_units"] * trend * week * year * (1.0 + promo_lift) * correlated_noise
            units = _poisson(mean, count_rngs[terminal_id])
            if day_index < history_days:
                history.append(
                    {
                        "date": day_text,
                        "terminal_id": terminal_id,
                        "sku_id": config["sku_id"],
                        "demand_units": units,
                        "promotion_id": promo_id,
                        "planned_promotion_lift": promo_lift,
                    }
                )
            else:
                baseline_future[(day_text, terminal_id)] = units

    future: list[dict[str, Any]] = []
    future_dates = dates[history_days:]
    for scenario in config["scenarios"]:
        for day_number, day in enumerate(future_dates, start=1):
            for terminal_id in terminals:
                multiplier = 1.0
                for event in scenario["demand_events"]:
                    if event["start_day"] <= day_number <= event["end_day"] and (event["terminal_ids"] == "all" or terminal_id in event["terminal_ids"]):
                        multiplier *= event["multiplier"]
                day_text = day.isoformat()
                future.append(
                    {
                        "scenario_id": scenario["id"],
                        "date": day_text,
                        "terminal_id": terminal_id,
                        "sku_id": config["sku_id"],
                        "demand_units": _round_units(baseline_future[(day_text, terminal_id)] * multiplier),
                    }
                )
    return calendar, history, future


def generate_payloads(config: dict[str, Any]) -> dict[str, bytes]:
    """Return the deterministic generated files, before any write to disk."""
    nodes, inventory, edges = _build_network(config)
    calendar, history, future = _build_demand(config, nodes)
    first_future = date.fromisoformat(config["history"]["start_date"]) + timedelta(days=config["history"]["days"])
    network = {
        "schema_version": SCHEMA_VERSION,
        "sku_id": config["sku_id"],
        "edges": edges,
        "supplier_production_capacity_units_per_day": {
            row["node_id"]: row["daily_production_capacity_units"] for row in nodes if row["node_type"] == "supplier"
        },
        "timing_convention": {
            "day_order": ["receive_due_shipments", "place_orders_and_dispatch_using_prior_demand", "realize_terminal_demand", "close_inventory_and_costs"],
            "arrival_rule": "A dispatch on day d with lead_time_days L arrives at the beginning of day d+L; L >= 1.",
            "delay_event_basis": "Transport delay events apply by dispatch day and add extra_lead_time_days.",
        },
    }
    costs = {
        "schema_version": SCHEMA_VERSION,
        "sku_id": config["sku_id"],
        "costs": config["costs"],
        "accounting_rules": {
            "external_procurement": "Charge once when units enter the supplier from an external source; never on internal transfers.",
            "holding": "Charge closing on-hand units at every node once per day.",
            "shortage": "Charge only unfulfilled terminal consumer demand as lost sales; upstream unfilled requests are not additional lost sales.",
            "transport": "Charge variable cost per dispatched unit and fixed cost per actual dispatch.",
            "orders": "Charge fixed order cost only for actual positive replenishment orders.",
        },
    }
    scenarios = {
        "schema_version": SCHEMA_VERSION,
        "simulation_start_date": first_future.isoformat(),
        "horizon_days": config["simulation"]["horizon_days"],
        "base_scenario_id": "baseline",
        "demand_events_materialized_in_future_demand_csv": True,
        "simulator_must_not_reapply_demand_events": True,
        "visibility": {
            "scenario_config_and_future_demand": "simulator_and_evaluator_only",
            "planner": "Only prior observed demand, current observed node state, known calendar and disruptions disclosed by the simulator up to the current day are available.",
        },
        "scenarios": config["scenarios"],
    }
    return {
        "effective_config.json": json_bytes(config),
        "nodes.csv": csv_bytes(NODE_FIELDS, nodes),
        "initial_inventory.csv": csv_bytes(INVENTORY_FIELDS, inventory),
        "known_calendar.csv": csv_bytes(CALENDAR_FIELDS, calendar),
        "demand_history.csv": csv_bytes(HISTORY_FIELDS, history),
        "future_demand.csv": csv_bytes(FUTURE_FIELDS, future),
        "network_config.json": json_bytes(network),
        "cost_config.json": json_bytes(costs),
        "scenario_config.json": json_bytes(scenarios),
    }
