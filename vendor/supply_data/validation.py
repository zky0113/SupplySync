"""Validate configuration and written CSV/JSON inputs before simulation uses them."""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from . import SCHEMA_VERSION
from .io_utils import json_bytes, read_csv, read_json, sha256_bytes, sha256_file
from .schema import (
    CALENDAR_FIELDS,
    EDGE_TIERS,
    FUTURE_FIELDS,
    HISTORY_FIELDS,
    INVENTORY_FIELDS,
    NODE_FIELDS,
    NODE_TYPES,
    TIER_INDEX,
)


CORE_FILES = (
    "effective_config.json",
    "nodes.csv",
    "initial_inventory.csv",
    "known_calendar.csv",
    "demand_history.csv",
    "future_demand.csv",
    "network_config.json",
    "cost_config.json",
    "scenario_config.json",
)


def _need(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _number(value: Any, label: str, *, positive: bool = False) -> float:
    _need(isinstance(value, (int, float)) and not isinstance(value, bool), f"{label}: expected a number")
    result = float(value)
    _need(math.isfinite(result), f"{label}: must be finite")
    _need(result > 0 if positive else result >= 0, f"{label}: must be {'positive' if positive else 'non-negative'}")
    return result


def _integer(value: Any, label: str, *, lower: int = 0, upper: int | None = None) -> int:
    _need(isinstance(value, int) and not isinstance(value, bool), f"{label}: expected an integer")
    _need(value >= lower and (upper is None or value <= upper), f"{label}: outside allowed range")
    return value


def _csv_int(row: dict[str, str], field: str, label: str, *, lower: int = 0) -> int:
    try:
        value = int(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}.{field}: expected integer") from exc
    _need(value >= lower, f"{label}.{field}: must be >= {lower}")
    return value


def _csv_float(row: dict[str, str], field: str, label: str, *, lower: float = 0.0) -> float:
    try:
        value = float(row[field])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{label}.{field}: expected number") from exc
    _need(math.isfinite(value) and value >= lower, f"{label}.{field}: invalid value")
    return value


def _target_ids(value: Any, terminals: set[str], label: str) -> set[str]:
    if value == "all":
        return set(terminals)
    _need(isinstance(value, list) and bool(value), f"{label}: expected nonempty terminal list or 'all'")
    _need(all(isinstance(item, str) for item in value), f"{label}: terminal IDs must be text")
    _need(len(set(value)) == len(value) and set(value) <= terminals, f"{label}: duplicate or unknown terminal")
    return set(value)


def _event_days(event: dict[str, Any], horizon: int, label: str) -> range:
    first = _integer(event.get("start_day"), f"{label}.start_day", lower=1, upper=horizon)
    last = _integer(event.get("end_day"), f"{label}.end_day", lower=first, upper=horizon)
    return range(first, last + 1)


def validate_config(config: dict[str, Any]) -> None:
    """Reject malformed or business-incoherent source configuration."""
    _need(isinstance(config, dict), "config: expected JSON object")
    _need(config.get("schema_version") == SCHEMA_VERSION, "config.schema_version: unsupported")
    _integer(config.get("seed"), "config.seed", lower=0)
    _need(isinstance(config.get("sku_id"), str) and bool(config["sku_id"].strip()), "config.sku_id: required")
    try:
        history_start = date.fromisoformat(config["history"]["start_date"])
        history_days = _integer(config["history"]["days"], "history.days", lower=56, upper=2000)
        horizon = _integer(config["simulation"]["horizon_days"], "simulation.horizon_days", lower=1, upper=90)
        raw_nodes = config["network"]["nodes"]
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"config: history, simulation or network missing/invalid: {exc}") from exc
    _need(isinstance(raw_nodes, list) and bool(raw_nodes), "network.nodes: nonempty list required")
    _need(all(isinstance(item, dict) for item in raw_nodes), "network.nodes: all nodes must be objects")
    ids = [item.get("id") for item in raw_nodes]
    _need(all(isinstance(item, str) and bool(item) and "|" not in item and "," not in item for item in ids), "network.nodes: invalid node ID")
    _need(len(set(ids)) == len(ids), "network.nodes: duplicate node ID")
    node_specs = {item["id"]: item for item in raw_nodes}
    suppliers = [item for item in raw_nodes if item.get("type") == "supplier"]
    _need(len(suppliers) == 1, "network.nodes: exactly one supplier is required")
    supplier_id = suppliers[0]["id"]
    terminals = {item["id"] for item in raw_nodes if item.get("type") == "terminal"}
    _need(bool(terminals), "network.nodes: at least one terminal required")
    children: dict[str, list[str]] = defaultdict(list)
    edges: set[tuple[str, str]] = set()
    for item in raw_nodes:
        node_id = item["id"]
        node_type = item.get("type")
        _need(node_type in NODE_TYPES, f"node {node_id}: invalid type")
        parent_id = item.get("upstream_id")
        if node_type == "supplier":
            _need(parent_id is None, f"node {node_id}: supplier cannot have upstream")
        else:
            _need(parent_id in node_specs, f"node {node_id}: missing/unknown upstream")
            parent_type = node_specs[parent_id].get("type")
            _need(parent_type in TIER_INDEX and TIER_INDEX[node_type] == TIER_INDEX[parent_type] + 1, f"node {node_id}: edge must join adjacent tiers")
            children[parent_id].append(node_id)
            edges.add((parent_id, node_id))
        if node_type == "terminal":
            _integer(item.get("base_daily_demand_units"), f"node {node_id}.base_daily_demand_units", lower=1)
        else:
            _need("base_daily_demand_units" not in item, f"node {node_id}: only terminals have consumer demand")
    _need({item["type"] for item in raw_nodes} == set(NODE_TYPES), "network.nodes: all four tiers required")
    for item in raw_nodes:
        if item["type"] != "terminal":
            _need(bool(children[item["id"]]), f"node {item['id']}: nonterminal has no downstream node")
    reached = {supplier_id}
    frontier = [supplier_id]
    while frontier:
        next_id = frontier.pop()
        for child_id in children[next_id]:
            _need(child_id not in reached, "network.nodes: cycle or duplicated path")
            reached.add(child_id)
            frontier.append(child_id)
    _need(reached == set(ids), "network.nodes: orphan node")

    tier_params = config["network"].get("tier_parameters", {})
    _need(isinstance(tier_params, dict), "network.tier_parameters: expected object")
    _need(set(tier_params) == set(NODE_TYPES), "network.tier_parameters: missing or extra tier")
    for node_type in NODE_TYPES:
        params = tier_params[node_type]
        _need(isinstance(params, dict), f"network.tier_parameters.{node_type}: expected object")
        initial_cover = _number(params.get("initial_cover_days"), f"{node_type}.initial_cover_days", positive=True)
        capacity_cover = _number(params.get("capacity_cover_days"), f"{node_type}.capacity_cover_days", positive=True)
        _need(capacity_cover >= initial_cover, f"{node_type}: capacity cover must not be below initial cover")
        _number(params.get("handling_capacity_multiplier"), f"{node_type}.handling_capacity_multiplier", positive=True)
    _number(tier_params["supplier"].get("production_capacity_multiplier"), "supplier.production_capacity_multiplier", positive=True)
    transport = config["network"].get("transport_by_tier", {})
    _need(isinstance(transport, dict), "network.transport_by_tier: expected object")
    _need(set(transport) == set(EDGE_TIERS), "network.transport_by_tier: missing or extra tier")
    for edge_tier in EDGE_TIERS:
        params = transport[edge_tier]
        _need(isinstance(params, dict), f"network.transport_by_tier.{edge_tier}: expected object")
        _integer(params.get("lead_time_days"), f"{edge_tier}.lead_time_days", lower=1, upper=14)
        _number(params.get("capacity_multiplier"), f"{edge_tier}.capacity_multiplier", positive=True)
        minimum = _integer(params.get("minimum_order_units"), f"{edge_tier}.minimum_order_units", lower=1)
        multiple = _integer(params.get("order_multiple_units"), f"{edge_tier}.order_multiple_units", lower=1)
        _need(minimum % multiple == 0, f"{edge_tier}: minimum order must be a multiple of order_multiple_units")

    demand = config.get("demand", {})
    _need(isinstance(demand, dict), "demand: expected object")
    trend = demand.get("daily_trend_rate")
    _need(isinstance(trend, (int, float)) and not isinstance(trend, bool) and math.isfinite(trend) and -0.01 <= trend <= 0.01, "demand.daily_trend_rate: unreasonable")
    weekly = demand.get("weekday_multipliers_monday_first")
    _need(isinstance(weekly, list) and len(weekly) == 7, "demand.weekday_multipliers: seven values required")
    for weekday, multiplier in enumerate(weekly):
        _need(0.5 <= _number(multiplier, f"weekday[{weekday}]") <= 1.5, f"weekday[{weekday}]: outside business range")
    _need(_number(demand.get("annual_amplitude"), "demand.annual_amplitude") <= 0.3, "annual_amplitude: outside business range")
    _integer(demand.get("annual_peak_day_of_year"), "demand.annual_peak_day_of_year", lower=1, upper=366)
    for shock_name in ("global_shock", "regional_shock", "terminal_shock"):
        shock = demand.get(shock_name, {})
        _need(isinstance(shock, dict), f"{shock_name}: expected object")
        phi = shock.get("ar_phi")
        _need(isinstance(phi, (int, float)) and not isinstance(phi, bool) and math.isfinite(phi) and -0.95 < phi < 0.95, f"{shock_name}.ar_phi: invalid")
        _need(_number(shock.get("stationary_sigma"), f"{shock_name}.stationary_sigma") <= 0.3, f"{shock_name}.stationary_sigma: too large")
    promotions = demand.get("promotions")
    _need(isinstance(promotions, list), "demand.promotions: expected list")
    _need(all(isinstance(item, dict) for item in promotions), "demand.promotions: entries must be objects")
    promo_ids: set[str] = set()
    promo_marks: set[tuple[date, str]] = set()
    last_date = history_start + timedelta(days=history_days + horizon - 1)
    for promotion in promotions:
        promo_id = promotion.get("id")
        _need(isinstance(promo_id, str) and bool(promo_id) and promo_id not in promo_ids, "demand.promotions: duplicate/invalid promotion ID")
        promo_ids.add(promo_id)
        first = date.fromisoformat(promotion["start_date"])
        last = date.fromisoformat(promotion["end_date"])
        _need(first <= last, f"promotion {promo_id}: end before start")
        _need(_number(promotion.get("lift"), f"promotion {promo_id}.lift") <= 1.0, f"promotion {promo_id}: lift too large")
        targets = _target_ids(promotion.get("terminal_ids"), terminals, f"promotion {promo_id}")
        visible_first = max(first, history_start)
        visible_last = min(last, last_date)
        for offset in range(max(0, (visible_last - visible_first).days + 1)):
            day = visible_first + timedelta(days=offset)
            for terminal_id in targets:
                mark = (day, terminal_id)
                _need(mark not in promo_marks, f"promotion {promo_id}: overlapping promotions for {terminal_id} on {day}")
                promo_marks.add(mark)

    costs = config.get("costs", {})
    _need(isinstance(costs, dict), "costs: expected object")
    _need(costs.get("currency") == "CNY", "costs.currency: expected CNY")
    _number(costs.get("external_procurement_cost_per_unit"), "costs.external_procurement_cost_per_unit")
    _number(costs.get("terminal_lost_sale_penalty_per_unit"), "costs.terminal_lost_sale_penalty_per_unit")
    for key, expected in (
        ("holding_cost_per_unit_per_day_by_type", set(NODE_TYPES)),
        ("transport_variable_cost_per_unit_by_tier", set(EDGE_TIERS)),
        ("transport_fixed_cost_per_dispatch_by_tier", set(EDGE_TIERS)),
        ("order_fixed_cost_per_order_by_tier", set(EDGE_TIERS)),
    ):
        values = costs.get(key)
        _need(isinstance(values, dict) and set(values) == expected, f"costs.{key}: missing/extra keys")
        for item_key, value in values.items():
            _number(value, f"costs.{key}.{item_key}")

    scenarios = config.get("scenarios")
    _need(isinstance(scenarios, list) and bool(scenarios), "scenarios: nonempty list required")
    _need(all(isinstance(item, dict) for item in scenarios), "scenarios: entries must be objects")
    scenario_ids = [scenario.get("id") for scenario in scenarios]
    _need(all(isinstance(item, str) and bool(item) for item in scenario_ids) and len(set(scenario_ids)) == len(scenario_ids), "scenarios: duplicate/invalid ID")
    _need(scenario_ids[0] == "baseline", "scenarios: baseline must be first")
    for scenario in scenarios:
        scenario_id = scenario["id"]
        for key in ("demand_events", "transport_delay_events", "supply_limit_events"):
            _need(isinstance(scenario.get(key), list), f"scenario {scenario_id}.{key}: expected list")
            _need(all(isinstance(event, dict) for event in scenario[key]), f"scenario {scenario_id}.{key}: entries must be objects")
        if scenario_id == "baseline":
            _need(not any(scenario[key] for key in ("demand_events", "transport_delay_events", "supply_limit_events")), "baseline scenario must be unperturbed")
        demand_marks: set[tuple[int, str]] = set()
        for event in scenario["demand_events"]:
            days = _event_days(event, horizon, f"scenario {scenario_id}.demand_event")
            targets = _target_ids(event.get("terminal_ids"), terminals, f"scenario {scenario_id}.demand_event")
            multiplier = _number(event.get("multiplier"), f"scenario {scenario_id}.demand_multiplier", positive=True)
            _need(multiplier <= 3.0, f"scenario {scenario_id}: demand multiplier too large")
            for day in days:
                for terminal_id in targets:
                    mark = (day, terminal_id)
                    _need(mark not in demand_marks, f"scenario {scenario_id}: overlapping demand events")
                    demand_marks.add(mark)
        delay_marks: set[tuple[int, str, str]] = set()
        for event in scenario["transport_delay_events"]:
            days = _event_days(event, horizon, f"scenario {scenario_id}.transport_delay")
            edge = (event.get("from_node"), event.get("to_node"))
            _need(edge in edges, f"scenario {scenario_id}: unknown transport edge {edge}")
            _integer(event.get("extra_lead_time_days"), f"scenario {scenario_id}.extra_lead_time_days", lower=1, upper=14)
            for day in days:
                mark = (day, *edge)
                _need(mark not in delay_marks, f"scenario {scenario_id}: overlapping transport delays")
                delay_marks.add(mark)
        supply_marks: set[tuple[int, str]] = set()
        for event in scenario["supply_limit_events"]:
            days = _event_days(event, horizon, f"scenario {scenario_id}.supply_limit")
            _need(event.get("supplier_id") == supplier_id, f"scenario {scenario_id}: unknown supplier")
            multiplier = _number(event.get("capacity_multiplier"), f"scenario {scenario_id}.capacity_multiplier", positive=True)
            _need(multiplier <= 1.0, f"scenario {scenario_id}: supply restriction multiplier must be <= 1")
            for day in days:
                mark = (day, supplier_id)
                _need(mark not in supply_marks, f"scenario {scenario_id}: overlapping supply limits")
                supply_marks.add(mark)


def _daily_date_set(start: date, count: int) -> set[str]:
    return {(start + timedelta(days=offset)).isoformat() for offset in range(count)}


def _half_up_units(value: float) -> int:
    return max(0, math.floor(value + 0.5))


def validate_dataset(output_dir: Path, config: dict[str, Any], *, verify_manifest: bool = True) -> dict[str, Any]:
    """Reload every generated file; reject broken relations, types, counts and hashes."""
    validate_config(config)
    nodes = read_csv(output_dir / "nodes.csv", NODE_FIELDS)
    inventory = read_csv(output_dir / "initial_inventory.csv", INVENTORY_FIELDS)
    calendar = read_csv(output_dir / "known_calendar.csv", CALENDAR_FIELDS)
    history = read_csv(output_dir / "demand_history.csv", HISTORY_FIELDS)
    future = read_csv(output_dir / "future_demand.csv", FUTURE_FIELDS)
    effective_config = read_json(output_dir / "effective_config.json")
    network = read_json(output_dir / "network_config.json")
    costs = read_json(output_dir / "cost_config.json")
    scenarios = read_json(output_dir / "scenario_config.json")
    _need(isinstance(network, dict) and isinstance(costs, dict) and isinstance(scenarios, dict), "generated JSON: expected objects")
    _need(effective_config == config, "effective_config.json: differs from the config used for validation")
    raw_specs = {item["id"]: item for item in config["network"]["nodes"]}
    expected_ids = set(raw_specs)
    node_by_id: dict[str, dict[str, str]] = {}
    for row in nodes:
        node_id = row["node_id"]
        _need(node_id in expected_ids and node_id not in node_by_id, f"nodes.csv: unknown/duplicate node {node_id}")
        node_by_id[node_id] = row
        spec = raw_specs[node_id]
        _need(row["node_type"] == spec["type"], f"nodes.csv: type mismatch for {node_id}")
        _need(row["upstream_node_id"] == (spec["upstream_id"] or ""), f"nodes.csv: upstream mismatch for {node_id}")
        _need(_csv_int(row, "tier", node_id) == TIER_INDEX[spec["type"]], f"nodes.csv: tier mismatch for {node_id}")
        _need(row["sku_id"] == config["sku_id"], f"nodes.csv: SKU mismatch for {node_id}")
        _csv_int(row, "nominal_daily_flow_units", node_id, lower=1)
        capacity = _csv_int(row, "storage_capacity_units", node_id, lower=1)
        stock = _csv_int(row, "initial_on_hand_units", node_id)
        _need(stock <= capacity, f"nodes.csv: stock above capacity for {node_id}")
        _csv_int(row, "daily_processing_capacity_units", node_id, lower=1)
        production = _csv_int(row, "daily_production_capacity_units", node_id)
        _need((production > 0) == (spec["type"] == "supplier"), f"nodes.csv: supplier production mismatch for {node_id}")
        expected_children = sorted(item["id"] for item in raw_specs.values() if item["upstream_id"] == node_id)
        _need(row["downstream_node_ids"] == "|".join(expected_children), f"nodes.csv: downstream mismatch for {node_id}")
    _need(set(node_by_id) == expected_ids, "nodes.csv: missing node")
    for node_id, row in node_by_id.items():
        expected_flow = (
            raw_specs[node_id]["base_daily_demand_units"] if row["node_type"] == "terminal"
            else sum(int(node_by_id[child_id]["nominal_daily_flow_units"]) for child_id in row["downstream_node_ids"].split("|") if child_id)
        )
        _need(int(row["nominal_daily_flow_units"]) == expected_flow, f"nodes.csv: incoherent nominal flow for {node_id}")

    history_start = date.fromisoformat(config["history"]["start_date"])
    history_days = config["history"]["days"]
    horizon = config["simulation"]["horizon_days"]
    future_start = history_start + timedelta(days=history_days)
    expected_state_date = (future_start - timedelta(days=1)).isoformat()
    inv_by_node: dict[str, dict[str, str]] = {}
    for row in inventory:
        node_id = row["node_id"]
        _need(node_id in node_by_id and node_id not in inv_by_node, f"initial_inventory.csv: unknown/duplicate node {node_id}")
        inv_by_node[node_id] = row
        _need(row["state_date"] == expected_state_date and row["sku_id"] == config["sku_id"], f"initial_inventory.csv: state date/SKU mismatch for {node_id}")
        on_hand = _csv_int(row, "on_hand_units", node_id)
        _need(on_hand == int(node_by_id[node_id]["initial_on_hand_units"]), f"initial_inventory.csv: node stock mismatch for {node_id}")
        _need(on_hand <= int(node_by_id[node_id]["storage_capacity_units"]), f"initial_inventory.csv: stock above capacity for {node_id}")
        for field in ("reserved_units", "backorder_units", "in_transit_inbound_units"):
            _need(_csv_int(row, field, node_id) == 0, f"initial_inventory.csv: {field} must be zero under this startup convention")
    _need(set(inv_by_node) == expected_ids, "initial_inventory.csv: missing node")

    _need(network.get("schema_version") == SCHEMA_VERSION and network.get("sku_id") == config["sku_id"], "network_config.json: schema/SKU mismatch")
    edges = network.get("edges")
    _need(isinstance(edges, list), "network_config.json: edges missing")
    _need(all(isinstance(edge, dict) for edge in edges), "network_config.json: edge entries must be objects")
    expected_edges = {(item["upstream_id"], item["id"]) for item in raw_specs.values() if item["upstream_id"] is not None}
    edge_by_pair: dict[tuple[str, str], dict[str, Any]] = {}
    for edge in edges:
        pair = (edge.get("from_node"), edge.get("to_node"))
        _need(pair in expected_edges and pair not in edge_by_pair, f"network_config.json: unknown/duplicate edge {pair}")
        edge_by_pair[pair] = edge
        tier = f"{raw_specs[pair[0]]['type']}_to_{raw_specs[pair[1]]['type']}"
        _need(edge.get("edge_tier") == tier, f"network_config.json: tier mismatch for {pair}")
        params = config["network"]["transport_by_tier"][tier]
        lead = _integer(edge.get("base_lead_time_days"), f"edge {pair}.lead", lower=1, upper=14)
        _need(lead == params["lead_time_days"], f"network_config.json: lead differs from config for {pair}")
        transport_capacity = _integer(edge.get("transport_capacity_units_per_day"), f"edge {pair}.capacity", lower=1)
        expected_capacity = math.ceil(int(node_by_id[pair[1]]["nominal_daily_flow_units"]) * params["capacity_multiplier"])
        _need(transport_capacity == expected_capacity, f"network_config.json: capacity differs from config for {pair}")
        _need(transport_capacity >= int(node_by_id[pair[1]]["nominal_daily_flow_units"]), f"network_config.json: baseline transport bottleneck on {pair}")
        minimum = _integer(edge.get("minimum_order_units"), f"edge {pair}.minimum", lower=1)
        multiple = _integer(edge.get("order_multiple_units"), f"edge {pair}.multiple", lower=1)
        _need(minimum == params["minimum_order_units"] and multiple == params["order_multiple_units"], f"network_config.json: order constraints differ from config for {pair}")
    _need(set(edge_by_pair) == expected_edges, "network_config.json: missing edge")
    supplier_id = next(item["id"] for item in raw_specs.values() if item["type"] == "supplier")
    _need(network.get("supplier_production_capacity_units_per_day") == {supplier_id: int(node_by_id[supplier_id]["daily_production_capacity_units"])}, "network_config.json: supplier production mismatch")
    total_nominal = sum(item["base_daily_demand_units"] for item in raw_specs.values() if item["type"] == "terminal")
    _need(int(node_by_id[supplier_id]["daily_production_capacity_units"]) >= total_nominal, "network_config.json: baseline supply below nominal demand")
    _need(isinstance(network.get("timing_convention"), dict), "network_config.json: timing convention missing")

    _need(costs.get("schema_version") == SCHEMA_VERSION and costs.get("sku_id") == config["sku_id"], "cost_config.json: schema/SKU mismatch")
    _need(costs.get("costs") == config["costs"], "cost_config.json: costs differ from source config")
    _need(isinstance(costs.get("accounting_rules"), dict), "cost_config.json: accounting rules missing")
    _need(scenarios.get("schema_version") == SCHEMA_VERSION and scenarios.get("simulation_start_date") == future_start.isoformat(), "scenario_config.json: schema/start mismatch")
    _need(scenarios.get("horizon_days") == horizon and scenarios.get("base_scenario_id") == "baseline", "scenario_config.json: horizon/base mismatch")
    _need(scenarios.get("scenarios") == config["scenarios"], "scenario_config.json: scenarios differ from source config")
    _need(scenarios.get("demand_events_materialized_in_future_demand_csv") is True and scenarios.get("simulator_must_not_reapply_demand_events") is True, "scenario_config.json: demand shock application rule missing")
    _need(scenarios.get("visibility", {}).get("scenario_config_and_future_demand") == "simulator_and_evaluator_only", "scenario_config.json: information boundary missing")

    terminals = {item["id"] for item in raw_specs.values() if item["type"] == "terminal"}
    historical_dates = _daily_date_set(history_start, history_days)
    future_dates = _daily_date_set(future_start, horizon)
    all_dates = historical_dates | future_dates
    cal_by_key: dict[tuple[str, str], dict[str, str]] = {}
    for row in calendar:
        key = (row["date"], row["terminal_id"])
        _need(row["date"] in all_dates and row["terminal_id"] in terminals and key not in cal_by_key, f"known_calendar.csv: unexpected/duplicate {key}")
        cal_by_key[key] = row
        _need(row["sku_id"] == config["sku_id"], "known_calendar.csv: SKU mismatch")
        _need(_csv_int(row, "weekday_monday_zero", str(key)) == date.fromisoformat(row["date"]).weekday(), f"known_calendar.csv: weekday mismatch {key}")
        lift = _csv_float(row, "planned_promotion_lift", str(key))
        matching_promotions = [
            promotion
            for promotion in config["demand"]["promotions"]
            if promotion["start_date"] <= row["date"] <= promotion["end_date"]
            and (promotion["terminal_ids"] == "all" or row["terminal_id"] in promotion["terminal_ids"])
        ]
        expected_promotion = matching_promotions[0] if matching_promotions else None
        _need(row["promotion_id"] == (expected_promotion["id"] if expected_promotion else ""), f"known_calendar.csv: promotion ID mismatch {key}")
        _need(abs(lift - (expected_promotion["lift"] if expected_promotion else 0.0)) < 1e-9, f"known_calendar.csv: promotion lift mismatch {key}")
    _need(len(cal_by_key) == len(all_dates) * len(terminals), "known_calendar.csv: missing terminal-day")
    history_by_key: dict[tuple[str, str], int] = {}
    for row in history:
        key = (row["date"], row["terminal_id"])
        _need(row["date"] in historical_dates and row["terminal_id"] in terminals and key not in history_by_key, f"demand_history.csv: unexpected/duplicate {key}")
        _need(row["sku_id"] == config["sku_id"], "demand_history.csv: SKU mismatch")
        history_by_key[key] = _csv_int(row, "demand_units", str(key))
        _need((row["promotion_id"], row["planned_promotion_lift"]) == (cal_by_key[key]["promotion_id"], cal_by_key[key]["planned_promotion_lift"]), f"demand_history.csv: promotion mismatch {key}")
    _need(len(history_by_key) == history_days * len(terminals), "demand_history.csv: missing terminal-day")
    scenario_by_id = {item["id"]: item for item in config["scenarios"]}
    future_by_key: dict[tuple[str, str, str], int] = {}
    for row in future:
        key = (row["scenario_id"], row["date"], row["terminal_id"])
        _need(row["scenario_id"] in scenario_by_id and row["date"] in future_dates and row["terminal_id"] in terminals and key not in future_by_key, f"future_demand.csv: unexpected/duplicate {key}")
        _need(row["sku_id"] == config["sku_id"], "future_demand.csv: SKU mismatch")
        future_by_key[key] = _csv_int(row, "demand_units", str(key))
    _need(len(future_by_key) == len(scenario_by_id) * horizon * len(terminals), "future_demand.csv: missing scenario terminal-day")
    for scenario_id, scenario in scenario_by_id.items():
        for offset in range(horizon):
            day_text = (future_start + timedelta(days=offset)).isoformat()
            day_number = offset + 1
            for terminal_id in terminals:
                baseline = future_by_key[("baseline", day_text, terminal_id)]
                multiplier = 1.0
                for event in scenario["demand_events"]:
                    if event["start_day"] <= day_number <= event["end_day"] and (event["terminal_ids"] == "all" or terminal_id in event["terminal_ids"]):
                        multiplier *= event["multiplier"]
                _need(future_by_key[(scenario_id, day_text, terminal_id)] == _half_up_units(baseline * multiplier), f"future_demand.csv: scenario multiplier mismatch on {scenario_id}/{day_text}/{terminal_id}")

    summary = _summarize(config, node_by_id, inventory, history_by_key, future_by_key, edge_by_pair)
    if verify_manifest:
        stored_summary = read_json(output_dir / "data_summary.json")
        _need(stored_summary == summary, "data_summary.json: does not match data files")
        manifest = read_json(output_dir / "generation_manifest.json")
        _need(manifest.get("schema_version") == SCHEMA_VERSION and manifest.get("seed") == config["seed"], "generation_manifest.json: version/seed mismatch")
        _need(manifest.get("config_sha256") == sha256_bytes(json_bytes(config)), "generation_manifest.json: source config hash mismatch")
        expected_files = set(CORE_FILES) | {"data_summary.json"}
        _need(set(manifest.get("file_sha256", {})) == expected_files, "generation_manifest.json: file inventory mismatch")
        for name in sorted(expected_files):
            _need(manifest["file_sha256"][name] == sha256_file(output_dir / name), f"generation_manifest.json: hash mismatch for {name}")
    return summary


def _summarize(
    config: dict[str, Any],
    node_by_id: dict[str, dict[str, str]],
    inventory: list[dict[str, str]],
    history: dict[tuple[str, str], int],
    future: dict[tuple[str, str, str], int],
    edges: dict[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    by_day: dict[str, int] = defaultdict(int)
    by_terminal: dict[str, list[int]] = defaultdict(list)
    by_weekday: dict[int, list[int]] = defaultdict(list)
    for (day_text, terminal_id), units in sorted(history.items()):
        by_day[day_text] += units
        by_terminal[terminal_id].append(units)
    for day_text, units in sorted(by_day.items()):
        by_weekday[date.fromisoformat(day_text).weekday()].append(units)
    daily_totals = list(by_day.values())
    stock_by_tier: dict[str, int] = defaultdict(int)
    count_by_tier: dict[str, int] = defaultdict(int)
    for row in inventory:
        node_type = node_by_id[row["node_id"]]["node_type"]
        stock_by_tier[node_type] += int(row["on_hand_units"])
        count_by_tier[node_type] += 1
    scenario_totals: dict[str, int] = defaultdict(int)
    for (scenario_id, _, _), units in future.items():
        scenario_totals[scenario_id] += units
    baseline_total = scenario_totals["baseline"]
    return {
        "schema_version": SCHEMA_VERSION,
        "seed": config["seed"],
        "sku_id": config["sku_id"],
        "history_start_date": config["history"]["start_date"],
        "history_days": config["history"]["days"],
        "future_start_date": (date.fromisoformat(config["history"]["start_date"]) + timedelta(days=config["history"]["days"])).isoformat(),
        "future_days": config["simulation"]["horizon_days"],
        "node_count": len(node_by_id),
        "node_count_by_type": {node_type: count_by_tier[node_type] for node_type in NODE_TYPES},
        "edge_count": len(edges),
        "terminal_history_rows": len(history),
        "future_demand_rows": len(future),
        "scenario_count": len(config["scenarios"]),
        "history_total_demand_units": sum(daily_totals),
        "history_daily_total_mean_units": round(statistics.mean(daily_totals), 2),
        "history_daily_total_std_units": round(statistics.pstdev(daily_totals), 2),
        "history_daily_total_min_units": min(daily_totals),
        "history_daily_total_max_units": max(daily_totals),
        "history_mean_units_per_terminal_per_day": {terminal_id: round(statistics.mean(units), 2) for terminal_id, units in sorted(by_terminal.items())},
        "history_mean_total_units_by_weekday_monday_first": [round(statistics.mean(by_weekday[weekday]), 2) for weekday in range(7)],
        "initial_on_hand_units_by_type": {node_type: stock_by_tier[node_type] for node_type in NODE_TYPES},
        "future_total_demand_units_by_scenario": dict(sorted(scenario_totals.items())),
        "future_demand_change_vs_baseline_pct": {
            scenario_id: round(100.0 * (total / baseline_total - 1.0), 2) if baseline_total else None
            for scenario_id, total in sorted(scenario_totals.items())
        },
    }
