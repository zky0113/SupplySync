"""Replenishment policies: decentralised (S1) and privacy-aware collaborative (S2).

Both policies use a periodic-review order-up-to policy every day.  The
difference is the *demand signal* each tier plans against:

* **S1 (decentralised)** -- every node only sees the orders placed by its
  immediate downstream.  Upper tiers therefore forecast the *amplified order
  stream*, not the true consumer demand.  This is the classic setting that
  produces the bullwhip effect.

* **S2 (collaborative, data-minimising)** -- each node shares only an
  *aggregate forecast* of the consumer demand in its subtree with its
  upstream neighbour.  Individual terminal demand series never leave the
  node; upstream sees a single summed number per planning window.  This is a
  data-minimisation / least-authorisation prototype, NOT a formal privacy
  guarantee (aggregated statistics can still leak information; no
  differential-privacy or k-anonymity proof is claimed).

Orders respect the edge minimum order, order multiple and node storage
capacity.  Transport / processing / production capacity are enforced by the
simulator at fulfilment time.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

from .data_loader import Dataset
from .forecast import TerminalForecast, aggregate_forecast, aggregate_std


@dataclass
class PolicyState:
    """Mutable, per-policy state the simulator updates each day."""

    on_hand: dict[str, int] = field(default_factory=dict)
    in_transit_inbound: dict[str, int] = field(default_factory=dict)
    # Units the buyer has ordered from upstream but the seller has not yet
    # shipped.  This is part of the buyer's inventory position so that the
    # buyer does not re-order the same gap while an order is pending.
    ordered_unshipped: dict[str, int] = field(default_factory=dict)
    # Units each node owes to its downstream (backorders from below).
    backorders_owed: dict[str, int] = field(default_factory=dict)
    # (seller, buyer) -> list of (day_number, units) for orders placed by buyer.
    observed_orders: dict[tuple[str, str], list[tuple[int, int]]] = field(default_factory=dict)
    # For S1: each node's running demand-level estimate from observed orders.
    demand_level_estimate: dict[str, float] = field(default_factory=dict)
    demand_std_estimate: dict[str, float] = field(default_factory=dict)


class Policy(Protocol):
    name: str          # display name, e.g. "S1_decentralised"
    policy_id: str     # stable identifier, e.g. "S1" / "S2"

    def decide(self, day_number: int, state: PolicyState) -> list[tuple[str, str, int]]:
        """Return orders as (buyer, seller, units) to place on ``day_number``."""
        ...


def _round_order(units: float, minimum: int, multiple: int) -> int:
    """Round a target quantity to a feasible order size.

    Returns 0 when the target is below the minimum order quantity, otherwise
    the largest multiple of ``multiple`` that is at least ``minimum`` and at
    most ``units`` (rounded to nearest multiple with a minimum-order floor).
    """
    if units < minimum:
        return 0
    steps = math.floor(units / multiple)
    qty = steps * multiple
    if qty < minimum:
        qty = minimum
    return qty


def _inventory_position(state: PolicyState, node_id: str) -> int:
    """Inventory position = on-hand + ordered-not-received - owed-to-downstream.

    ``ordered-not-received`` includes both in-transit (shipped) and
    ordered-but-not-yet-shipped, so a pending order prevents re-ordering the
    same gap.  ``owed-to-downstream`` is what this node still has to ship to
    its children.
    """
    return (
        state.on_hand.get(node_id, 0)
        + state.in_transit_inbound.get(node_id, 0)
        + state.ordered_unshipped.get(node_id, 0)
        - state.backorders_owed.get(node_id, 0)
    )


class _BaseOrderUpTo:
    """Shared order-up-to machinery for both policies."""

    def __init__(self, dataset: Dataset, forecasts: dict[str, TerminalForecast], *, service_z: float = 2.6, max_order_days: float = 4.0):
        self.dataset = dataset
        self.forecasts = forecasts
        self.service_z = service_z
        self.max_order_days = max_order_days
        self.demand_level_estimate: dict[str, float] = {}
        self.demand_std_estimate: dict[str, float] = {}

    def _lead_time(self, buyer: str) -> int:
        node = self.dataset.nodes[buyer]
        if node.upstream_node_id:
            edge = self.dataset.edges_by_pair[(node.upstream_node_id, buyer)]
            return edge.base_lead_time_days
        return 0

    def _build_order(self, buyer: str, seller: str, demand_forecast_window: float, safety: float, state: PolicyState, daily_demand: float) -> tuple[str, str, int]:
        edge = self.dataset.edges_by_pair[(seller, buyer)]
        order_up_to = demand_forecast_window + safety
        position = _inventory_position(state, buyer)
        target = order_up_to - position
        # Storage-capacity guard: the maximum we can physically receive is the
        # remaining physical space (capacity minus on-hand minus in-transit).
        # We deliberately do NOT subtract backorders here because backorders
        # represent units that will leave the node, freeing space.
        cap = self.dataset.nodes[buyer].storage_capacity_units
        physical_occupied = state.on_hand.get(buyer, 0) + state.in_transit_inbound.get(buyer, 0)
        max_allowed = cap - physical_occupied
        if max_allowed < 0:
            max_allowed = 0
        target = min(target, float(max_allowed))
        if daily_demand > 0:
            target = min(target, daily_demand * self.max_order_days)
        units = _round_order(target, edge.minimum_order_units, edge.order_multiple_units)
        return (buyer, seller, units)


class DecentralisedPolicy(_BaseOrderUpTo):
    """S1: each tier forecasts the orders it receives from downstream."""

    name = "S1_decentralised"
    policy_id = "S1"

    def __init__(self, dataset: Dataset, forecasts: dict[str, TerminalForecast], **kwargs: object):
        super().__init__(dataset, forecasts, **kwargs)  # type: ignore[arg-type]
        for node_id, node in dataset.nodes.items():
            if node.node_type == "supplier":
                continue
            self.demand_level_estimate[node_id] = float(node.nominal_daily_flow_units)
            self.demand_std_estimate[node_id] = float(node.nominal_daily_flow_units) * 0.15

    def _update_forecast_from_orders(self, buyer: str, seller: str, state: PolicyState, day_number: int) -> tuple[float, float]:
        """Forecast the buyer's own incoming demand.

        Terminals use the SAME day-specific consumer-demand forecast as S2
        (fair comparison).  Upper tiers forecast from the observed downstream
        order stream.  To avoid overestimating intermittent ordering, the
        observed-orders series is expanded to one observation per day
        (including zero-order days) before taking the moving average.
        """
        node = self.dataset.nodes[buyer]
        if node.node_type == "terminal":
            # Fair: same day-specific forecast as S2 uses for terminals.
            fc = self.forecasts[buyer]
            lead = self._lead_time(buyer)
            window_vals = [
                fc.mean_by_day.get(day_number + offset, 0.0)
                for offset in range(lead + 1)
            ]
            level = sum(window_vals) / len(window_vals) if window_vals else 0.0
            return level, fc.std

        child_ids = node.downstream_node_ids
        window = 10
        child_levels: list[float] = []
        child_stds: list[float] = []
        for child_id in child_ids:
            edge_key = (buyer, child_id)
            observations = state.observed_orders.get(edge_key, [])
            if not observations:
                continue
            # Expand to a daily series ending at the CURRENT decision day
            # (not the last observed day), so that stretches of zero-order
            # days are included and pull the estimate down.  Without this,
            # a single 100-unit order followed by 10 quiet days would keep
            # the estimate at 100 forever.
            daily: dict[int, int] = {}
            for d, u in observations:
                daily[d] = daily.get(d, 0) + u
            window_start = max(1, day_number - window + 1)
            series = [daily.get(d, 0) for d in range(window_start, day_number + 1)]
            if series:
                mean = sum(series) / len(series)
                child_levels.append(mean)
                if len(series) > 1:
                    var = sum((v - mean) ** 2 for v in series) / (len(series) - 1)
                    child_stds.append(math.sqrt(var))

        if not child_levels:
            return self.demand_level_estimate[buyer], self.demand_std_estimate[buyer]

        level = sum(child_levels)
        std = math.sqrt(sum(s * s for s in child_stds)) if child_stds else self.demand_std_estimate[buyer]
        prev_level = self.demand_level_estimate[buyer]
        level = 0.25 * level + 0.75 * prev_level
        cap = 5.0 * node.nominal_daily_flow_units
        level = min(level, cap)
        self.demand_level_estimate[buyer] = level
        self.demand_std_estimate[buyer] = std
        return level, std

    def decide(self, day_number: int, state: PolicyState) -> list[tuple[str, str, int]]:
        orders: list[tuple[str, str, int]] = []
        tiers = [self.dataset.terminal_ids, self.dataset.city_ids, self.dataset.regional_ids]
        for tier_nodes in tiers:
            for buyer in tier_nodes:
                node = self.dataset.nodes[buyer]
                seller = node.upstream_node_id
                if not seller:
                    continue
                level, std = self._update_forecast_from_orders(buyer, seller, state, day_number)
                lead = self._lead_time(buyer)
                window = level * float(lead + 1)
                safety = self.service_z * std * math.sqrt(lead + 1)
                order = self._build_order(buyer, seller, window, safety, state, daily_demand=level)
                if order[2] > 0:
                    orders.append(order)
                    state.observed_orders.setdefault((seller, buyer), []).append((day_number, order[2]))
        return orders


class CollaborativePolicy(_BaseOrderUpTo):
    """S2: every tier plans against the shared aggregate consumer-demand forecast.

    Data minimisation: only the *sum* of terminal forecasts in a subtree
    crosses a node boundary; individual terminal demand data stays local.
    This is a least-authorisation prototype, not a formal privacy proof.
    """

    name = "S2_collaborative"
    policy_id = "S2"

    def __init__(self, dataset: Dataset, forecasts: dict[str, TerminalForecast], **kwargs: object):
        super().__init__(dataset, forecasts, **kwargs)  # type: ignore[arg-type]
        self.node_forecast: dict[str, dict[int, float]] = {}
        self.node_std: dict[str, float] = {}
        for node_id in dataset.nodes:
            terminals = dataset.subtree_terminals(node_id)
            self.node_forecast[node_id] = aggregate_forecast(forecasts, terminals)
            self.node_std[node_id] = aggregate_std(forecasts, terminals)

    def decide(self, day_number: int, state: PolicyState) -> list[tuple[str, str, int]]:
        orders: list[tuple[str, str, int]] = []
        tiers = [self.dataset.terminal_ids, self.dataset.city_ids, self.dataset.regional_ids]
        for tier_nodes in tiers:
            for buyer in tier_nodes:
                node = self.dataset.nodes[buyer]
                seller = node.upstream_node_id
                if not seller:
                    continue
                lead = self._lead_time(buyer)
                forecast = self.node_forecast[buyer]
                window = sum(
                    forecast.get(day_number + offset, 0.0)
                    for offset in range(lead + 1)
                    if day_number + offset <= self.dataset.horizon_days
                )
                remaining = (lead + 1) - sum(1 for offset in range(lead + 1) if day_number + offset <= self.dataset.horizon_days)
                if remaining > 0:
                    last_day = self.dataset.horizon_days
                    window += forecast.get(last_day, 0.0) * remaining
                safety = self.service_z * self.node_std[buyer] * math.sqrt(lead + 1)
                daily_demand = sum(forecast.values()) / len(forecast) if forecast else 0.0
                order = self._build_order(buyer, seller, window, safety, state, daily_demand=daily_demand)
                if order[2] > 0:
                    orders.append(order)
                    state.observed_orders.setdefault((seller, buyer), []).append((day_number, order[2]))
        return orders


def build_policy(name: str, dataset: Dataset, forecasts: dict[str, TerminalForecast]) -> Policy:
    if name in ("S1", "S1_decentralised"):
        return DecentralisedPolicy(dataset, forecasts)
    if name in ("S2", "S2_collaborative"):
        return CollaborativePolicy(dataset, forecasts)
    raise ValueError(f"unknown policy: {name}")
