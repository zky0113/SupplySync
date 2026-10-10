"""Load the generated data set into typed structures for the simulator.

Only standard-library helpers are used so the package runs everywhere the
generator runs.  ``data/`` is treated as read-only input.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Node:
    node_id: str
    node_type: str
    tier: int
    upstream_node_id: str
    downstream_node_ids: tuple[str, ...]
    sku_id: str
    nominal_daily_flow_units: int
    initial_on_hand_units: int
    storage_capacity_units: int
    daily_processing_capacity_units: int
    daily_production_capacity_units: int


@dataclass(frozen=True)
class Edge:
    from_node: str
    to_node: str
    edge_tier: str
    base_lead_time_days: int
    transport_capacity_units_per_day: int
    minimum_order_units: int
    order_multiple_units: int


@dataclass(frozen=True)
class InventoryState:
    state_date: str
    node_id: str
    sku_id: str
    on_hand_units: int
    reserved_units: int
    backorder_units: int
    in_transit_inbound_units: int


@dataclass
class CalendarEntry:
    date: str
    terminal_id: str
    sku_id: str
    weekday_monday_zero: int
    promotion_id: str
    planned_promotion_lift: float


@dataclass
class HistoryDemand:
    date: str
    terminal_id: str
    sku_id: str
    demand_units: int
    promotion_id: str
    planned_promotion_lift: float


@dataclass(frozen=True)
class DemandEvent:
    start_day: int
    end_day: int
    multiplier: float
    terminal_ids: tuple[str, ...]


@dataclass(frozen=True)
class TransportDelayEvent:
    start_day: int
    end_day: int
    from_node: str
    to_node: str
    extra_lead_time_days: int


@dataclass(frozen=True)
class SupplyLimitEvent:
    start_day: int
    end_day: int
    supplier_id: str
    capacity_multiplier: float


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    description: str
    demand_events: tuple[DemandEvent, ...]
    transport_delay_events: tuple[TransportDelayEvent, ...]
    supply_limit_events: tuple[SupplyLimitEvent, ...]


@dataclass
class Dataset:
    nodes: dict[str, Node]
    edges_by_pair: dict[tuple[str, str], Edge]
    edges_from: dict[str, list[Edge]]
    edges_to: dict[str, list[Edge]]
    terminal_ids: list[str]
    city_ids: list[str]
    regional_ids: list[str]
    supplier_id: str
    descendants: dict[str, list[str]]  # all terminals below a node (recursive)
    initial_inventory: dict[str, int]  # node_id -> on_hand
    history_start: date
    history_days: int
    future_start: date
    horizon_days: int
    history_demand: dict[tuple[str, str], HistoryDemand]  # (date, terminal)
    calendar: dict[tuple[str, str], CalendarEntry]  # (date, terminal)
    future_demand: dict[tuple[str, str, str], int]  # (scenario, date, terminal)
    scenarios: dict[str, Scenario]
    costs: dict[str, Any]
    supplier_production_capacity: dict[str, int]

    def subtree_terminals(self, node_id: str) -> list[str]:
        return self.descendants[node_id]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as stream:
        return json.load(stream)


def _split_ids(text: str) -> tuple[str, ...]:
    return tuple(item for item in text.split("|") if item)


def load_dataset(data_dir: Path) -> Dataset:
    nodes_raw = _read_csv(data_dir / "nodes.csv")
    nodes: dict[str, Node] = {}
    for row in nodes_raw:
        node_id = row["node_id"]
        nodes[node_id] = Node(
            node_id=node_id,
            node_type=row["node_type"],
            tier=int(row["tier"]),
            upstream_node_id=row["upstream_node_id"],
            downstream_node_ids=_split_ids(row["downstream_node_ids"]),
            sku_id=row["sku_id"],
            nominal_daily_flow_units=int(row["nominal_daily_flow_units"]),
            initial_on_hand_units=int(row["initial_on_hand_units"]),
            storage_capacity_units=int(row["storage_capacity_units"]),
            daily_processing_capacity_units=int(row["daily_processing_capacity_units"]),
            daily_production_capacity_units=int(row["daily_production_capacity_units"]),
        )

    network = _read_json(data_dir / "network_config.json")
    edges_by_pair: dict[tuple[str, str], Edge] = {}
    edges_from: dict[str, list[Edge]] = {}
    edges_to: dict[str, list[Edge]] = {}
    for edge in network["edges"]:
        pair = (edge["from_node"], edge["to_node"])
        obj = Edge(
            from_node=edge["from_node"],
            to_node=edge["to_node"],
            edge_tier=edge["edge_tier"],
            base_lead_time_days=int(edge["base_lead_time_days"]),
            transport_capacity_units_per_day=int(edge["transport_capacity_units_per_day"]),
            minimum_order_units=int(edge["minimum_order_units"]),
            order_multiple_units=int(edge["order_multiple_units"]),
        )
        edges_by_pair[pair] = obj
        edges_from.setdefault(edge["from_node"], []).append(obj)
        edges_to.setdefault(edge["to_node"], []).append(obj)

    supplier_id = next(n.node_id for n in nodes.values() if n.node_type == "supplier")
    terminal_ids = sorted(n.node_id for n in nodes.values() if n.node_type == "terminal")
    city_ids = sorted(n.node_id for n in nodes.values() if n.node_type == "city_warehouse")
    regional_ids = sorted(n.node_id for n in nodes.values() if n.node_type == "regional_warehouse")

    # Recursive terminal descendants per node.
    descendants: dict[str, list[str]] = {}

    def collect(node_id: str) -> list[str]:
        node = nodes[node_id]
        if node.node_type == "terminal":
            return [node_id]
        result: list[str] = []
        for child in node.downstream_node_ids:
            result.extend(collect(child))
        return result

    for node_id in nodes:
        descendants[node_id] = collect(node_id)

    inv_raw = _read_csv(data_dir / "initial_inventory.csv")
    initial_inventory = {row["node_id"]: int(row["on_hand_units"]) for row in inv_raw}

    history_raw = _read_csv(data_dir / "demand_history.csv")
    history_demand: dict[tuple[str, str], HistoryDemand] = {}
    for row in history_raw:
        history_demand[(row["date"], row["terminal_id"])] = HistoryDemand(
            date=row["date"],
            terminal_id=row["terminal_id"],
            sku_id=row["sku_id"],
            demand_units=int(row["demand_units"]),
            promotion_id=row["promotion_id"],
            planned_promotion_lift=float(row["planned_promotion_lift"]),
        )

    calendar_raw = _read_csv(data_dir / "known_calendar.csv")
    calendar: dict[tuple[str, str], CalendarEntry] = {}
    for row in calendar_raw:
        calendar[(row["date"], row["terminal_id"])] = CalendarEntry(
            date=row["date"],
            terminal_id=row["terminal_id"],
            sku_id=row["sku_id"],
            weekday_monday_zero=int(row["weekday_monday_zero"]),
            promotion_id=row["promotion_id"],
            planned_promotion_lift=float(row["planned_promotion_lift"]),
        )

    future_raw = _read_csv(data_dir / "future_demand.csv")
    future_demand: dict[tuple[str, str, str], int] = {}
    for row in future_raw:
        future_demand[(row["scenario_id"], row["date"], row["terminal_id"])] = int(row["demand_units"])

    scenarios_raw = _read_json(data_dir / "scenario_config.json")
    future_start = date.fromisoformat(scenarios_raw["simulation_start_date"])
    horizon_days = int(scenarios_raw["horizon_days"])
    scenarios: dict[str, Scenario] = {}
    for sc in scenarios_raw["scenarios"]:
        scenarios[sc["id"]] = Scenario(
            scenario_id=sc["id"],
            description=sc["description"],
            demand_events=tuple(
                DemandEvent(
                    start_day=int(e["start_day"]),
                    end_day=int(e["end_day"]),
                    multiplier=float(e["multiplier"]),
                    terminal_ids=tuple(e["terminal_ids"]) if isinstance(e["terminal_ids"], list) else tuple(terminal_ids),
                )
                for e in sc["demand_events"]
            ),
            transport_delay_events=tuple(
                TransportDelayEvent(
                    start_day=int(e["start_day"]),
                    end_day=int(e["end_day"]),
                    from_node=e["from_node"],
                    to_node=e["to_node"],
                    extra_lead_time_days=int(e["extra_lead_time_days"]),
                )
                for e in sc["transport_delay_events"]
            ),
            supply_limit_events=tuple(
                SupplyLimitEvent(
                    start_day=int(e["start_day"]),
                    end_day=int(e["end_day"]),
                    supplier_id=e["supplier_id"],
                    capacity_multiplier=float(e["capacity_multiplier"]),
                )
                for e in sc["supply_limit_events"]
            ),
        )

    costs = _read_json(data_dir / "cost_config.json")["costs"]

    # Infer history window from the calendar (covers history + future).
    history_start = min(date.fromisoformat(k[0]) for k in calendar)
    history_days = (future_start - history_start).days

    return Dataset(
        nodes=nodes,
        edges_by_pair=edges_by_pair,
        edges_from=edges_from,
        edges_to=edges_to,
        terminal_ids=terminal_ids,
        city_ids=city_ids,
        regional_ids=regional_ids,
        supplier_id=supplier_id,
        descendants=descendants,
        initial_inventory=initial_inventory,
        history_start=history_start,
        history_days=history_days,
        future_start=future_start,
        horizon_days=horizon_days,
        history_demand=history_demand,
        calendar=calendar,
        future_demand=future_demand,
        scenarios=scenarios,
        costs=costs,
        supplier_production_capacity=dict(network["supplier_production_capacity_units_per_day"]),
    )


def future_date_at(dataset: Dataset, day_number: int) -> date:
    """Return the calendar date for a 1-based simulation day number."""
    return dataset.future_start + timedelta(days=day_number - 1)
