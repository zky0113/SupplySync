"""Pinned, independently checked adapter for the revised supply-chain engine.

The simulator owns future truth. Forecast/plan interfaces expose only history,
known calendar and the current policy state; no future demand is returned.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from functools import lru_cache
import hashlib
import json
from pathlib import Path
import sys

from .provider import Bundle


@dataclass(frozen=True)
class EngineContext:
    dataset: object
    forecasts: dict
    fingerprint: str
    snapshot_id: str


def verify_engine(root: Path, snapshot_id: str) -> str:
    manifest = json.loads((root / "engine_manifest.json").read_text(encoding="utf-8"))
    if manifest["display_snapshot_id"] != snapshot_id:
        raise ValueError("计算版本和展示快照不匹配。")
    expected = manifest["files"]
    if len(expected) != 14:
        raise ValueError("计算版本文件清单不完整。")
    for relative, digest in expected.items():
        file = root / relative
        if hashlib.sha256(file.read_bytes()).hexdigest() != digest:
            raise ValueError(f"计算输入或源码已变动：{relative}。请重新审核版本。")
        if relative.startswith("engine_input/") and relative != "engine_input/future_demand.csv":
            display = root / "data" / file.name
            if hashlib.sha256(display.read_bytes()).hexdigest() != digest:
                raise ValueError(f"展示输入与计算输入不一致：{file.name}")
    return hashlib.sha256(json.dumps(expected, sort_keys=True).encode()).hexdigest()[:16]


def context(bundle: Bundle) -> EngineContext:
    fingerprint = verify_engine(bundle.root, bundle.snapshot_id)
    return _load(str(bundle.root), bundle.snapshot_id, fingerprint)


@lru_cache(maxsize=2)
def _load(root_text: str, snapshot_id: str, fingerprint: str) -> EngineContext:
    root = Path(root_text)
    vendor = str(root / "vendor")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from supply_chain.data_loader import load_dataset
    from supply_chain.forecast import fit_all_terminal_forecasts
    dataset = load_dataset(root / "engine_input")
    forecasts = fit_all_terminal_forecasts(dataset)
    if len(forecasts) != len(dataset.terminal_ids) or dataset.horizon_days != 30:
        raise ValueError("预测或仿真窗口与审核版本不符。")
    return EngineContext(dataset, forecasts, fingerprint, snapshot_id)


def forecast_rows(ctx: EngineContext, node_id: str) -> list[dict]:
    from supply_chain.forecast import aggregate_forecast
    ds = ctx.dataset
    series = aggregate_forecast(ctx.forecasts, ds.subtree_terminals(node_id))
    return [{"date": (ds.future_start + timedelta(days=day-1)).isoformat(),
             "day": day, "predicted_units": round(series[day], 2)}
            for day in range(1, ds.horizon_days + 1)]


def holdout_score(ctx: EngineContext, node_id: str) -> dict:
    """Last 28 historical days, re-fit on preceding 182; no scored row in fit."""
    from supply_chain.forecast import fit_all_terminal_forecasts, aggregate_forecast
    ds = ctx.dataset
    days = 28
    train_days = ds.history_days - days
    cut = ds.history_start + timedelta(days=train_days)
    split = replace(ds, history_days=train_days, future_start=cut, horizon_days=days,
                    future_demand={})
    forecasts = fit_all_terminal_forecasts(split)
    descendants = ds.subtree_terminals(node_id)
    predicted = aggregate_forecast(forecasts, descendants)
    actuals, naive = [], []
    for day in range(1, days + 1):
        date = cut + timedelta(days=day-1)
        same_weekday = cut - timedelta(days=7) + timedelta(days=(day-1) % 7)
        actuals.append(sum(ds.history_demand[(date.isoformat(), t)].demand_units for t in descendants))
        naive.append(sum(ds.history_demand[(same_weekday.isoformat(), t)].demand_units for t in descendants))
    denominator = sum(actuals)
    model_wape = sum(abs(actuals[i] - predicted[i+1]) for i in range(days)) / denominator
    naive_wape = sum(abs(actuals[i] - naive[i]) for i in range(days)) / denominator
    return {"holdout_start": cut.isoformat(), "holdout_days": days,
            "model_wape": model_wape, "naive_wape": naive_wape,
            "actual_total": denominator,
            "model_mae": sum(abs(actuals[i] - predicted[i+1]) for i in range(days)) / days,
            "naive_mae": sum(abs(actuals[i] - naive[i]) for i in range(days)) / days}


def initial_plan(ctx: EngineContext, policy_id: str, node_id: str) -> dict:
    from supply_chain.policies import PolicyState, build_policy
    ds = ctx.dataset
    if policy_id not in ("S1", "S2") or node_id not in ds.nodes:
        raise ValueError("未知策略或节点。")
    policy = build_policy(policy_id, ds, ctx.forecasts)
    state = PolicyState(on_hand=dict(ds.initial_inventory),
        in_transit_inbound={node: 0 for node in ds.nodes},
        ordered_unshipped={node: 0 for node in ds.nodes},
        backorders_owed={node: 0 for node in ds.nodes}, observed_orders={},
        demand_level_estimate=dict(policy.demand_level_estimate),
        demand_std_estimate=dict(policy.demand_std_estimate))
    orders = policy.decide(1, state)
    selected = next(((buyer, seller, units) for buyer, seller, units in orders if buyer == node_id), None)
    upstream = ds.nodes[node_id].upstream_node_id
    edge = ds.edges_by_pair.get((upstream, node_id)) if upstream else None
    return {"policy": policy_id, "node_id": node_id, "upstream": upstream,
            "requested_units": selected[2] if selected else 0,
            "inventory_position": ds.initial_inventory[node_id],
            "capacity": ds.nodes[node_id].storage_capacity_units,
            "lead_days": edge.base_lead_time_days if edge else None,
            "minimum_order": edge.minimum_order_units if edge else None,
            "order_multiple": edge.order_multiple_units if edge else None,
            "note": "第 1 天事前建议下单量；实际发运、在途和到货由仿真决定。"}


def _audit_result(ds, result) -> None:
    if len(result.days) != ds.horizon_days or any(day.constraint_violations for day in result.days):
        raise ValueError("仿真天数或约束审计失败。")
    backlog = {pair: 0 for pair in ds.edges_by_pair}
    total_demand = total_sales = total_lost = 0
    total_cost = 0.0
    for day in result.days:
        for node, stock in day.on_hand.items():
            if stock < 0 or stock > ds.nodes[node].storage_capacity_units:
                raise ValueError(f"库存容量越界：{day.date}/{node}")
        for terminal in ds.terminal_ids:
            demand = day.terminal_demand[terminal]
            sales = day.terminal_sales[terminal]
            lost = day.terminal_lost_sales[terminal]
            if min(demand, sales, lost) < 0 or demand != sales + lost:
                raise ValueError("终端需求/销售/失销不守恒。")
            total_demand += demand; total_sales += sales; total_lost += lost
        for pair in backlog:
            backlog[pair] += day.orders_placed.get(pair, 0) - day.units_shipped.get(pair, 0)
            if backlog[pair] < 0 or backlog[pair] != day.backorders.get(pair, 0):
                raise ValueError(f"订单账本不守恒：{day.date}/{pair}")
        total_cost += (day.cost_procurement + day.cost_holding + day.cost_transport_variable
                       + day.cost_transport_fixed + day.cost_order_fixed + day.cost_shortage)
    if (total_demand, total_sales, total_lost) != (result.total_demand_units, result.total_sales_units, result.total_lost_sales_units):
        raise ValueError("仿真汇总需求与逐日记录不一致。")
    if abs(total_cost - result.total_cost) > 0.01:
        raise ValueError("成本分项与总成本不一致。")


def paired_experiment(ctx: EngineContext, scenario_id: str) -> dict:
    if scenario_id not in ctx.dataset.scenarios:
        raise ValueError("未知场景。")
    return _paired(ctx, scenario_id)


def _paired(ctx: EngineContext, scenario_id: str) -> dict:
    from supply_chain.policies import build_policy
    from supply_chain.simulator import run_simulation
    from supply_chain.metrics import compute_metrics
    ds = ctx.dataset
    results = {}
    for policy_id in ("S1", "S2"):
        policy = build_policy(policy_id, ds, ctx.forecasts)
        run = run_simulation(ds, ds.scenarios[scenario_id], policy, ctx.forecasts, warmup_days=0)
        _audit_result(ds, run)
        results[policy_id] = {"metrics": compute_metrics(ds, run).as_dict(), "run": run}
    left, right = results["S1"]["run"], results["S2"]["run"]
    if [(d.date, d.terminal_demand) for d in left.days] != [(d.date, d.terminal_demand) for d in right.days]:
        raise ValueError("策略对照没有使用相同外生需求。")
    run_id = hashlib.sha256(f"{ctx.fingerprint}:{ctx.snapshot_id}:{scenario_id}:S1:S2:warmup0".encode()).hexdigest()[:16]
    return {"run_id": run_id, "scenario_id": scenario_id, "snapshot_id": ctx.snapshot_id,
            "engine_fingerprint": ctx.fingerprint, "warmup_days": 0,
            "initial_inventory_equal": True, "daily_demand_equal": True,
            "S1": results["S1"], "S2": results["S2"]}


def public_result(experiment: dict) -> dict:
    """Export reproducible metrics and aggregates, never synthetic future-truth rows."""
    return {key: experiment[key] for key in ("run_id", "scenario_id", "snapshot_id",
            "engine_fingerprint", "warmup_days", "initial_inventory_equal", "daily_demand_equal")} | {
            pid: experiment[pid]["metrics"] for pid in ("S1", "S2")}


def daily_rows(experiment: dict, node_id: str) -> list[dict]:
    rows = []
    for policy_id in ("S1", "S2"):
        for day in experiment[policy_id]["run"].days:
            rows.append({"date": day.date, "day": day.day_number, "policy": policy_id,
                         "node_on_hand": day.on_hand[node_id],
                         "terminal_demand": sum(day.terminal_demand.values()),
                         "terminal_lost_sales": sum(day.terminal_lost_sales.values()),
                         "orders_placed": sum(day.orders_placed.values()),
                         "unshipped_backorders": sum(day.backorders.values()),
                         "daily_cost": round(day.cost_procurement + day.cost_holding
                             + day.cost_transport_variable + day.cost_transport_fixed
                             + day.cost_order_fixed + day.cost_shortage, 2)})
    return rows
