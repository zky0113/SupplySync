"""N-day multi-echelon simulation engine.

Implements the timing convention from ``network_config.json``::

    receive_due_shipments  ->  place_orders_and_dispatch  ->
    realize_terminal_demand  ->  close_inventory_and_costs

The simulator is policy-agnostic: it takes any ``Policy`` and reveals to it
only the state that would be observable up to the current day.  ``future_demand``
and scenario events are simulator-only (they are not passed to the policy).

Edge-level order ledger invariant per (seller, buyer) per day::

    end_of_day_unfulfilled = start_of_day_unfulfilled + new_orders - shipped

Orders and shipments are tracked separately so a pending (unshipped) order is
counted exactly once.  The policy's inventory position includes
``ordered_unshipped`` so it does not re-order the same gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from .data_loader import Dataset, Scenario, future_date_at
from .forecast import TerminalForecast
from .policies import Policy, PolicyState


@dataclass
class Shipment:
    seller: str
    buyer: str
    units: int
    dispatch_day: int
    arrival_day: int


@dataclass
class DayLog:
    day_number: int
    date: str
    terminal_demand: dict[str, int] = field(default_factory=dict)
    terminal_sales: dict[str, int] = field(default_factory=dict)
    terminal_lost_sales: dict[str, int] = field(default_factory=dict)
    on_hand: dict[str, int] = field(default_factory=dict)
    orders_placed: dict[tuple[str, str], int] = field(default_factory=dict)
    units_shipped: dict[tuple[str, str], int] = field(default_factory=dict)
    supplier_production: int = 0
    cost_procurement: float = 0.0
    cost_holding: float = 0.0
    cost_transport_variable: float = 0.0
    cost_transport_fixed: float = 0.0
    cost_order_fixed: float = 0.0
    cost_shortage: float = 0.0
    backorders: dict[tuple[str, str], int] = field(default_factory=dict)
    # Constraint violations recorded this day (for audit).  Empty == none.
    constraint_violations: list[str] = field(default_factory=list)


@dataclass
class SimulationResult:
    scenario_id: str
    policy_name: str
    policy_id: str
    days: list[DayLog] = field(default_factory=list)
    total_demand_units: int = 0
    total_sales_units: int = 0
    total_lost_sales_units: int = 0
    total_cost: float = 0.0
    daily_orders_by_tier: dict[str, list[int]] = field(default_factory=dict)

    @property
    def stockout_rate(self) -> float:
        return self.total_lost_sales_units / self.total_demand_units if self.total_demand_units else 0.0


def _effective_lead_time(dataset: Dataset, scenario: Scenario, seller: str, buyer: str, horizon_day: int) -> int:
    """Lead time for a dispatch made on ``horizon_day`` (measurement-day index).

    Scenario transport-delay events are defined relative to the measurement
    horizon (day 1 = first recorded day), so we query with ``horizon_day``.
    """
    edge = dataset.edges_by_pair[(seller, buyer)]
    extra = 0
    for event in scenario.transport_delay_events:
        if event.from_node == seller and event.to_node == buyer:
            if event.start_day <= horizon_day <= event.end_day:
                extra = max(extra, event.extra_lead_time_days)
    return edge.base_lead_time_days + extra


def _supply_capacity_multiplier(scenario: Scenario, supplier_id: str, horizon_day: int) -> float:
    multiplier = 1.0
    for event in scenario.supply_limit_events:
        if event.supplier_id == supplier_id and event.start_day <= horizon_day <= event.end_day:
            multiplier = min(multiplier, event.capacity_multiplier)
    return multiplier


def run_simulation(
    dataset: Dataset,
    scenario: Scenario,
    policy: Policy,
    forecasts: dict[str, TerminalForecast] | None = None,
    warmup_days: int = 0,
) -> SimulationResult:
    state = PolicyState(
        on_hand=dict(dataset.initial_inventory),
        in_transit_inbound={node_id: 0 for node_id in dataset.nodes},
        ordered_unshipped={node_id: 0 for node_id in dataset.nodes},
        backorders_owed={node_id: 0 for node_id in dataset.nodes},
        observed_orders={},
        demand_level_estimate=dict(getattr(policy, "demand_level_estimate", {})),
        demand_std_estimate=dict(getattr(policy, "demand_std_estimate", {})),
    )

    # Simulator-private state.
    in_transit: list[Shipment] = []
    # (seller, buyer) -> units the seller still owes the buyer (unshipped backorders).
    backorders: dict[tuple[str, str], int] = {}

    costs = dataset.costs
    holding_rate = costs["holding_cost_per_unit_per_day_by_type"]
    transport_var = costs["transport_variable_cost_per_unit_by_tier"]
    transport_fixed = costs["transport_fixed_cost_per_dispatch_by_tier"]
    order_fixed = costs["order_fixed_cost_per_order_by_tier"]
    procurement_unit = costs["external_procurement_cost_per_unit"]
    lost_sale_penalty = costs["terminal_lost_sale_penalty_per_unit"]

    result = SimulationResult(
        scenario_id=scenario.scenario_id,
        policy_name=policy.name,
        policy_id=getattr(policy, "policy_id", policy.name),
    )
    result.daily_orders_by_tier = {"supplier_to_regional": [], "regional_to_city": [], "city_to_terminal": []}

    # Warm-up demand proxy: full-horizon average per terminal.
    fc_demand = {}
    for tid in dataset.terminal_ids:
        means = list(forecasts[tid].mean_by_day.values()) if forecasts else []
        fc_demand[tid] = sum(means) / len(means) if means else 0.0

    def _allocate_proportional(seller: str, requests: list[tuple[str, int]], available: int,
                               log: DayLog) -> dict[str, int]:
        """Allocate ``available`` units among buyers proportionally to request size.

        Returns {buyer: shipped_units}.  Backorders (pre-existing unfulfilled)
        are treated as requests with the same priority as new orders, so a
        long-waiting buyer is not starved by a newer one.  Remainders go to
        the largest requests first.
        """
        if available <= 0 or not requests:
            return {}
        total_requested = sum(u for _, u in requests)
        if total_requested == 0:
            return {}
        if available >= total_requested:
            return {b: u for b, u in requests}
        # Proportional split with largest-remainder method.
        raw = {b: available * u / total_requested for b, u in requests}
        floor = {b: int(math.floor(v)) for b, v in raw.items()}
        allocated = sum(floor.values())
        remainder = available - allocated
        # Distribute the remainder by largest fractional part.
        frac_order = sorted(requests, key=lambda x: raw[x[0]] - floor[x[0]], reverse=True)
        for b, _ in frac_order:
            if remainder <= 0:
                break
            floor[b] += 1
            remainder -= 1
        return floor

    def _run_day(sim_day: int, horizon_day: int, record: bool) -> None:
        nonlocal in_transit
        day_date = future_date_at(dataset, horizon_day)
        day_text = day_date.isoformat()
        log = DayLog(day_number=horizon_day, date=day_text)

        # ---------------------------------------------------------------
        # 1. Receive shipments that arrive at the beginning of this day.
        # ---------------------------------------------------------------
        arriving = [s for s in in_transit if s.arrival_day == sim_day]
        in_transit = [s for s in in_transit if s.arrival_day != sim_day]
        for ship in arriving:
            cap = dataset.nodes[ship.buyer].storage_capacity_units
            space = cap - state.on_hand[ship.buyer]
            if ship.units > space:
                # Over-capacity arrival: accept what fits and defer the
                # remainder to the next day (material conservation -- units
                # are never lost).  This can happen when in-transit stock
                # was dispatched before a demand decline was observed.
                accepted = max(0, space)
                deferred = ship.units - accepted
                if deferred > 0:
                    in_transit.append(Shipment(
                        seller=ship.seller, buyer=ship.buyer, units=deferred,
                        dispatch_day=ship.dispatch_day, arrival_day=sim_day + 1,
                    ))
                log.constraint_violations.append(
                    f"over_capacity_arrival_deferred:{ship.buyer}:{deferred}"
                )
            else:
                accepted = ship.units
            state.on_hand[ship.buyer] += accepted

        # Recompute aggregate in-transit inbound from the remaining list.
        state.in_transit_inbound = {node_id: 0 for node_id in dataset.nodes}
        for ship in in_transit:
            state.in_transit_inbound[ship.buyer] += ship.units

        # ---------------------------------------------------------------
        # 2. Policy decides orders (uses state up to end of previous day).
        # ---------------------------------------------------------------
        orders = policy.decide(horizon_day, state)

        for buyer, seller, units in orders:
            if units <= 0:
                continue
            edge = dataset.edges_by_pair[(seller, buyer)]
            log.cost_order_fixed += order_fixed[edge.edge_tier]
            log.orders_placed[(seller, buyer)] = log.orders_placed.get((seller, buyer), 0) + units

        # ---------------------------------------------------------------
        # 2b. Fulfill orders and backorders, respecting capacities.
        # ---------------------------------------------------------------
        # New requests per seller.
        new_requests: dict[str, list[tuple[str, int]]] = {}
        for buyer, seller, units in orders:
            if units > 0:
                new_requests.setdefault(seller, []).append((buyer, units))

        # Supplier production.
        supplier_id = dataset.supplier_id
        supplier_cap = dataset.supplier_production_capacity[supplier_id]
        cap_mult = _supply_capacity_multiplier(scenario, supplier_id, horizon_day)
        effective_prod_cap = int(math.floor(supplier_cap * cap_mult))
        pending_at_supplier = (
            sum(backorders.get((supplier_id, b), 0) for b in dataset.regional_ids)
            + sum(u for _, u in new_requests.get(supplier_id, []))
        )
        production_needed = max(0, pending_at_supplier - state.on_hand[supplier_id])
        production = min(effective_prod_cap, production_needed)
        state.on_hand[supplier_id] += production
        log.supplier_production = production
        log.cost_procurement = production * procurement_unit

        edge_shipped_today: dict[tuple[str, str], int] = {}
        node_processed_today: dict[str, int] = {node_id: 0 for node_id in dataset.nodes}

        seller_order = [supplier_id] + dataset.regional_ids + dataset.city_ids

        for seller in seller_order:
            # Build the full pending list: existing backorders first, then new
            # requests.  Each buyer appears at most once: if a buyer has both
            # a backorder and a new request, they are summed.
            buyer_requests: dict[str, int] = {}
            for buyer in dataset.nodes[seller].downstream_node_ids:
                owed = backorders.get((seller, buyer), 0)
                if owed > 0:
                    buyer_requests[buyer] = buyer_requests.get(buyer, 0) + owed
            for buyer, units in new_requests.get(seller, []):
                buyer_requests[buyer] = buyer_requests.get(buyer, 0) + units

            if not buyer_requests:
                continue

            # Total the seller can ship today: stock + remaining processing +
            # remaining transport (per edge).  We first compute a global
            # pool = min(on_hand, processing_budget), then allocate
            # proportionally, then apply per-edge transport caps.
            processing_budget = (
                dataset.nodes[seller].daily_processing_capacity_units
                - node_processed_today[seller]
            )
            global_pool = min(state.on_hand[seller], processing_budget)

            requests_list = list(buyer_requests.items())
            alloc = _allocate_proportional(seller, requests_list, global_pool, log)

            for buyer, requested in requests_list:
                shipped = alloc.get(buyer, 0)
                edge = dataset.edges_by_pair[(seller, buyer)]
                already_on_edge = edge_shipped_today.get((seller, buyer), 0)
                transport_budget = edge.transport_capacity_units_per_day - already_on_edge
                shipped = min(shipped, transport_budget)
                if shipped < 0:
                    shipped = 0

                # Buyer receiving-capacity guard: do not ship more than the
                # buyer can physically hold (capacity minus on-hand minus
                # already-in-transit).  The rest stays as backorder.  This
                # prevents over-capacity arrivals and the resulting cascade
                # of deferred shipments.
                buyer_cap = dataset.nodes[buyer].storage_capacity_units
                buyer_occupied = (
                    state.on_hand.get(buyer, 0)
                    + state.in_transit_inbound.get(buyer, 0)
                )
                buyer_space = max(0, buyer_cap - buyer_occupied)
                shipped = min(shipped, buyer_space)

                # Cap at what was requested.
                shipped = min(shipped, requested)

                # ALWAYS update the backorder ledger, even when shipped == 0,
                # so that an unfilled order is recorded as a backorder
                # (instead of silently disappearing).  The invariant is:
                #   end_of_day_backorder = requested - shipped
                # where ``requested`` already includes any pre-existing
                # backorder for this (seller, buyer) pair.
                backorders[(seller, buyer)] = requested - shipped

                if shipped <= 0:
                    # Nothing to ship this round; the full request stays as
                    # a backorder (already recorded above).
                    continue

                state.on_hand[seller] -= shipped
                node_processed_today[seller] += shipped
                edge_shipped_today[(seller, buyer)] = already_on_edge + shipped
                log.units_shipped[(seller, buyer)] = log.units_shipped.get((seller, buyer), 0) + shipped

                lead = _effective_lead_time(dataset, scenario, seller, buyer, horizon_day)
                arrival_day = sim_day + lead
                in_transit.append(Shipment(seller=seller, buyer=buyer, units=shipped,
                                           dispatch_day=sim_day, arrival_day=arrival_day))
                state.in_transit_inbound[buyer] += shipped

                log.cost_transport_variable += shipped * transport_var[edge.edge_tier]
                if already_on_edge == 0:
                    log.cost_transport_fixed += transport_fixed[edge.edge_tier]

        # Clean up zero backorders.
        for key in list(backorders.keys()):
            if backorders[key] <= 0:
                del backorders[key]

        # Update policy state: ordered_unshipped = backorders owed TO this
        # node by its upstream (i.e. this node's unfilled purchase orders).
        state.ordered_unshipped = {node_id: 0 for node_id in dataset.nodes}
        for (seller, buyer), owed in backorders.items():
            state.ordered_unshipped[buyer] += owed
        # backorders_owed = what this node owes to its downstream.
        state.backorders_owed = {node_id: 0 for node_id in dataset.nodes}
        for (seller, buyer), owed in backorders.items():
            state.backorders_owed[seller] += owed

        # ---------------------------------------------------------------
        # 3. Realize terminal consumer demand.
        # ---------------------------------------------------------------
        day_demand = 0
        day_sales = 0
        day_lost = 0
        for terminal_id in dataset.terminal_ids:
            if record:
                demand = dataset.future_demand[(scenario.scenario_id, day_text, terminal_id)]
            else:
                demand = int(round(fc_demand[terminal_id]))
            on_hand = state.on_hand[terminal_id]
            # Terminal daily processing capacity caps how many units can be
            # sold in one day (even if stock is available).
            processing_cap = dataset.nodes[terminal_id].daily_processing_capacity_units
            sellable = min(on_hand, processing_cap)
            sales = min(demand, sellable)
            lost = demand - sales
            state.on_hand[terminal_id] = on_hand - sales
            log.terminal_demand[terminal_id] = demand
            log.terminal_sales[terminal_id] = sales
            log.terminal_lost_sales[terminal_id] = lost
            day_demand += demand
            day_sales += sales
            day_lost += lost
        log.cost_shortage = day_lost * lost_sale_penalty
        if record:
            result.total_demand_units += day_demand
            result.total_sales_units += day_sales
            result.total_lost_sales_units += day_lost

        # ---------------------------------------------------------------
        # 4. Close inventory: holding cost and constraint audit.
        # ---------------------------------------------------------------
        for node_id, node in dataset.nodes.items():
            on_hand = state.on_hand[node_id]
            log.on_hand[node_id] = on_hand
            if on_hand > node.storage_capacity_units:
                log.constraint_violations.append(
                    f"over_capacity_eod:{node_id}:{on_hand - node.storage_capacity_units}"
                )
            log.cost_holding += on_hand * holding_rate[node.node_type]

        for key, owed in backorders.items():
            log.backorders[key] = owed

        if record:
            day_total = (
                log.cost_procurement + log.cost_holding + log.cost_transport_variable
                + log.cost_transport_fixed + log.cost_order_fixed + log.cost_shortage
            )
            result.total_cost += day_total
            result.days.append(log)

            result.daily_orders_by_tier["city_to_terminal"].append(
                sum(log.orders_placed.get((s, b), 0) for s in dataset.city_ids for b in dataset.nodes[s].downstream_node_ids)
            )
            result.daily_orders_by_tier["regional_to_city"].append(
                sum(log.orders_placed.get((s, b), 0) for s in dataset.regional_ids for b in dataset.nodes[s].downstream_node_ids)
            )
            result.daily_orders_by_tier["supplier_to_regional"].append(
                sum(log.orders_placed.get((s, b), 0) for s in [supplier_id] for b in dataset.nodes[s].downstream_node_ids)
            )

    # ---- Warm-up phase: fill the pipeline without recording metrics. ----
    for sim_day in range(1, warmup_days + 1):
        _run_day(sim_day, horizon_day=1, record=False)

    # ---- Measurement phase: the N-day horizon. ----
    for sim_day in range(warmup_days + 1, warmup_days + dataset.horizon_days + 1):
        horizon_day = sim_day - warmup_days
        _run_day(sim_day, horizon_day=horizon_day, record=True)

    return result
