"""Compute evaluation metrics from a simulation result.

Metrics reported per (scenario, policy) pair so S1 and S2 can be compared:

* **Cost** -- total and by component (procurement, holding, transport, order
  fixed, shortage penalty).
* **Stockout rate** -- lost-sales units divided by realised terminal demand.
* **Inventory turnover** -- total terminal *sales* units divided by the
  average on-hand inventory units across the horizon.  The numerator uses
  actual sales (not demand) so unfulfilled demand does not inflate turnover.
* **Bullwhip coefficient** -- at each echelon, ``Var(orders) / Var(consumer
  demand)``.  This measures how much that echelon's order stream amplifies
  the original consumer-demand variability.  Adjacent-layer ratios
  (``Var(upper orders) / Var(lower orders)``) are also reported as a
  diagnostic.  Values > 1 mean amplification.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .data_loader import Dataset
from .simulator import SimulationResult


@dataclass
class Metrics:
    scenario_id: str
    policy_name: str
    policy_id: str
    total_cost: float
    cost_procurement: float
    cost_holding: float
    cost_transport: float
    cost_order_fixed: float
    cost_shortage: float
    stockout_rate: float
    inventory_turnover: float
    avg_on_hand_units: float
    bullwhip_by_tier: dict[str, float]
    bullwhip_adjacent: dict[str, float]
    bullwhip_chain_avg: float

    def as_dict(self) -> dict[str, object]:
        return {
            "scenario_id": self.scenario_id,
            "policy_name": self.policy_name,
            "policy_id": self.policy_id,
            "total_cost": round(self.total_cost, 2),
            "cost_procurement": round(self.cost_procurement, 2),
            "cost_holding": round(self.cost_holding, 2),
            "cost_transport": round(self.cost_transport, 2),
            "cost_order_fixed": round(self.cost_order_fixed, 2),
            "cost_shortage": round(self.cost_shortage, 2),
            "stockout_rate": round(self.stockout_rate, 4),
            "inventory_turnover": round(self.inventory_turnover, 3),
            "avg_on_hand_units": round(self.avg_on_hand_units, 1),
            "bullwhip_by_tier": {k: round(v, 3) for k, v in self.bullwhip_by_tier.items()},
            "bullwhip_adjacent": {k: round(v, 3) for k, v in self.bullwhip_adjacent.items()},
            "bullwhip_chain_avg": round(self.bullwhip_chain_avg, 3),
        }


def _variance(values: list[int]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return sum((v - mean) ** 2 for v in values) / len(values)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def compute_metrics(dataset: Dataset, result: SimulationResult) -> Metrics:
    cost_procurement = sum(d.cost_procurement for d in result.days)
    cost_holding = sum(d.cost_holding for d in result.days)
    cost_transport = sum(d.cost_transport_variable + d.cost_transport_fixed for d in result.days)
    cost_order_fixed = sum(d.cost_order_fixed for d in result.days)
    cost_shortage = sum(d.cost_shortage for d in result.days)
    total_cost = result.total_cost

    stockout_rate = result.stockout_rate

    # Average on-hand inventory across all nodes and all days.
    daily_on_hand = [sum(d.on_hand.values()) for d in result.days]
    avg_on_hand = _mean(daily_on_hand)
    # Turnover = actual sales / average on-hand.
    inventory_turnover = result.total_sales_units / avg_on_hand if avg_on_hand > 0 else 0.0

    # Consumer demand variance (the reference signal).
    daily_consumer_demand = [sum(d.terminal_demand.values()) for d in result.days]
    consumer_variance = _variance(daily_consumer_demand)

    orders_terminal = result.daily_orders_by_tier["city_to_terminal"]
    orders_city = result.daily_orders_by_tier["regional_to_city"]
    orders_regional = result.daily_orders_by_tier["supplier_to_regional"]

    # Primary bullwhip: each tier's order variance / consumer-demand variance.
    bullwhip_by_tier: dict[str, float] = {}
    if consumer_variance > 0:
        bullwhip_by_tier["terminal"] = _variance(orders_terminal) / consumer_variance
        bullwhip_by_tier["city"] = _variance(orders_city) / consumer_variance
        bullwhip_by_tier["regional"] = _variance(orders_regional) / consumer_variance
    else:
        bullwhip_by_tier = {"terminal": 0.0, "city": 0.0, "regional": 0.0}

    # Adjacent-layer amplification (diagnostic): upper / lower.
    bullwhip_adjacent: dict[str, float] = {}
    var_t = _variance(orders_terminal)
    var_c = _variance(orders_city)
    var_r = _variance(orders_regional)
    bullwhip_adjacent["city_over_terminal"] = (var_c / var_t) if var_t > 0 else 0.0
    bullwhip_adjacent["regional_over_city"] = (var_r / var_c) if var_c > 0 else 0.0

    bullwhip_chain_avg = _mean(list(bullwhip_by_tier.values()))

    return Metrics(
        scenario_id=result.scenario_id,
        policy_name=result.policy_name,
        policy_id=result.policy_id,
        total_cost=total_cost,
        cost_procurement=cost_procurement,
        cost_holding=cost_holding,
        cost_transport=cost_transport,
        cost_order_fixed=cost_order_fixed,
        cost_shortage=cost_shortage,
        stockout_rate=stockout_rate,
        inventory_turnover=inventory_turnover,
        avg_on_hand_units=avg_on_hand,
        bullwhip_by_tier=bullwhip_by_tier,
        bullwhip_adjacent=bullwhip_adjacent,
        bullwhip_chain_avg=bullwhip_chain_avg,
    )
