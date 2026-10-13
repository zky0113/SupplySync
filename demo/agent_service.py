"""Bounded tool-using supply-chain assistant, with optional DeepSeek LLM routing.

No API secret is stored here. Evidence mode remains usable without a key and
does not claim to be a language model. Live mode uses OpenAI-compatible tool
calling and allows only four read-only, validated business tools.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

from .engine import forecast_rows, holdout_score, initial_plan, paired_experiment, public_result

TOOL_NAMES = ("read_inventory", "forecast_demand", "recommend_replenishment", "compare_strategies")


def execute_tool(ctx, name: str, args: dict) -> dict:
    ds = ctx.dataset
    node = args.get("node_id")
    if name != "compare_strategies" and node not in ds.nodes:
        raise ValueError("请选择有效的供应链节点。")
    if name == "read_inventory":
        info = ds.nodes[node]
        return {"node_id": node, "node_type": info.node_type,
                "initial_on_hand_units": ds.initial_inventory[node],
                "capacity_units": info.storage_capacity_units,
                "upstream": info.upstream_node_id,
                "downstream": list(info.downstream_node_ids), "state": "initial_only"}
    if name == "forecast_demand":
        rows = forecast_rows(ctx, node)
        return {"node_id": node, "forecast_first_7_days": rows[:7],
                "forecast_30_day_total": round(sum(row["predicted_units"] for row in rows), 1),
                "historical_holdout": holdout_score(ctx, node),
                "boundary": "history and known calendar only; no future actual demand"}
    if name == "recommend_replenishment":
        policy = args.get("policy_id", "S2")
        if policy not in ("S1", "S2"):
            raise ValueError("策略只能为 S1 或 S2。")
        return initial_plan(ctx, policy, node)
    if name == "compare_strategies":
        scenario = args.get("scenario_id")
        if scenario not in ds.scenarios:
            raise ValueError("请选择有效的实验场景。")
        return public_result(paired_experiment(ctx, scenario))
    raise ValueError("未授权的工具调用。")


def evidence_answer(ctx, question: str, node_id: str, scenario_id: str) -> dict:
    """Limited keyword routing for offline demos; never presented as an LLM."""
    if not question.strip():
        raise ValueError("请输入业务问题。")
    trace = []
    def call(name, args):
        result = execute_tool(ctx, name, args)
        trace.append({"tool": name, "arguments": args, "result": result})
        return result

    stock_words = ("库存", "现货", "仓容", "状态")
    forecast_words = ("需求", "预测", "误差", "WAPE")
    plan_words = ("补货", "建议", "订单", "下单")
    compare_words = ("场景", "成本", "缺货", "对比", "协同", "牛鞭", "周转", "效果")
    want_stock = any(word in question for word in stock_words)
    want_forecast = any(word in question for word in forecast_words)
    want_plan = any(word in question for word in plan_words)
    want_compare = any(word in question for word in compare_words)
    if not any((want_stock, want_forecast, want_plan, want_compare)):
        want_stock = want_forecast = want_plan = want_compare = True
    # A plan needs the current stock and the demand forecast as its evidence.
    if want_plan:
        want_stock = want_forecast = True
    parts = []
    comparison = None
    if want_stock:
        inventory = call("read_inventory", {"node_id": node_id})
        parts.append(f"{node_id} 初始现货 {inventory['initial_on_hand_units']} 件，仓容 {inventory['capacity_units']} 件。")
    if want_forecast:
        forecast = call("forecast_demand", {"node_id": node_id})
        parts.append(f"未来 30 天预测需求约 {forecast['forecast_30_day_total']:.1f} 件；"
                     f"历史末 28 天留出验证 WAPE {forecast['historical_holdout']['model_wape']:.1%}"
                     f"（季节朴素基线 {forecast['historical_holdout']['naive_wape']:.1%}）。")
    if want_plan:
        plan = call("recommend_replenishment", {"node_id": node_id, "policy_id": "S2"})
        parts.append(f"S2 首日建议向 {plan['upstream'] or '上游'} 下单 {plan['requested_units']} 件；"
                     "这是请求量，不等于发货量。")
    if want_compare:
        comparison = call("compare_strategies", {"scenario_id": scenario_id})
        m1, m2 = comparison["S1"], comparison["S2"]
        cost_delta = m2["total_cost"] - m1["total_cost"]
        direction = "下降" if cost_delta < 0 else "上升"
        parts.append(f"{scenario_id} 的 30 天仿真：S1 总成本 {m1['total_cost']:,.2f} 元、"
                     f"缺货率 {m1['stockout_rate']:.2%}；S2 总成本 {m2['total_cost']:,.2f} 元、"
                     f"缺货率 {m2['stockout_rate']:.2%}；S2 成本相对 S1 {direction} "
                     f"{abs(cost_delta):,.2f} 元。运行编号 {comparison['run_id']}。")
    parts.append("只支持关键词范围内的证据汇总；合成结果不代表真实企业收益。")
    return {"mode": "规则工具编排（非大模型）", "answer": "".join(parts), "trace": trace,
            "run_id": comparison["run_id"] if comparison else None}


def _schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


TOOLS = [
    _schema("read_inventory", "Read one node's initial inventory and capacity", {"node_id": {"type": "string"}}, ["node_id"]),
    _schema("forecast_demand", "Forecast demand from history and known calendar", {"node_id": {"type": "string"}}, ["node_id"]),
    _schema("recommend_replenishment", "Compute day-one requested order", {
        "node_id": {"type": "string"}, "policy_id": {"type": "string", "enum": ["S1", "S2"]}}, ["node_id", "policy_id"]),
    _schema("compare_strategies", "Run audited paired 30-day simulation for one scenario", {
        "scenario_id": {"type": "string"}}, ["scenario_id"]),
]


def _deepseek_chat(api_key: str, messages: list[dict]) -> dict:
    request = urllib.request.Request("https://api.deepseek.com/chat/completions",
        data=json.dumps({"model": "deepseek-chat", "messages": messages, "tools": TOOLS,
                         "tool_choice": "auto", "temperature": 0}).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)["choices"][0]["message"]
    except (urllib.error.URLError, KeyError, IndexError, ValueError) as error:
        raise RuntimeError(f"模型接口不可用或返回无效响应：{type(error).__name__}") from error


def live_answer(ctx, question: str, node_id: str, scenario_id: str, api_key: str) -> dict:
    """At most 8 tool calls / 4 rounds; final answer requires tool evidence."""
    if not api_key:
        raise ValueError("未配置 DEEPSEEK_API_KEY。")
    messages = [{"role": "system", "content":
        "你是供应链分析助手。只可依赖业务工具返回的数值。必须先调用工具；"
        "工具返回 error 代表失败，不能作为业务证据；未成功获取的数据不得推断数值或结论。"
        "不可编造预测、成本、改善率或因果结论。首日订单是请求量，不是到货量。"
        "仿真是合成数据，不可表述为企业实际收益。可说明不确定性，简洁回答中文。"},
        {"role": "user", "content": f"当前节点={node_id}；场景={scenario_id}。问题：{question[:1500]}"}]
    trace = []
    for _ in range(4):
        message = _deepseek_chat(api_key, messages)
        if not isinstance(message, dict):
            raise RuntimeError("模型返回无效消息。")
        calls = message.get("tool_calls")
        if calls is None:
            calls = []
        if not isinstance(calls, list):
            raise RuntimeError("模型返回无效工具调用列表。")
        if not calls:
            if not trace:
                raise RuntimeError("模型未调用业务工具；拒绝无证据回答。")
            if not any(item["success"] for item in trace):
                raise RuntimeError("业务工具均调用失败；拒绝无证据回答。")
            answer = message.get("content")
            if not isinstance(answer, str) or not answer.strip():
                raise RuntimeError("模型未给出可展示答案。")
            comparison = next((item["result"] for item in reversed(trace)
                               if item["tool"] == "compare_strategies" and item["success"]), None)
            return {"mode": "DeepSeek 工具调用", "answer": answer, "trace": trace,
                    "run_id": comparison.get("run_id") if comparison else None}
        if len(trace) + len(calls) > 8:
            raise RuntimeError("工具调用超过安全上限。")
        for call in calls:
            if (not isinstance(call, dict) or not isinstance(call.get("id"), str)
                    or not call["id"].strip() or not isinstance(call.get("function"), dict)):
                raise RuntimeError("模型返回无效工具调用。")
        messages.append({"role": "assistant", "content": message.get("content"), "tool_calls": calls})
        for call in calls:
            name = call["function"].get("name")
            if name not in TOOL_NAMES:
                raise RuntimeError("模型请求未授权工具。")
            args = {}
            try:
                args = json.loads(call["function"]["arguments"])
                if not isinstance(args, dict):
                    raise ValueError("参数必须是对象")
                # Restrict scope to the user's selected node and scenario.
                if ("node_id" in args and args["node_id"] != node_id) or (
                    "scenario_id" in args and args["scenario_id"] != scenario_id):
                    raise ValueError("工具参数超出当前选定范围")
                result = execute_tool(ctx, name, args)
                if not isinstance(result, dict) or not result:
                    raise ValueError("业务工具返回无效结果")
            except (KeyError, TypeError, ValueError, RuntimeError, json.JSONDecodeError) as error:
                result = {"error": str(error)}
            success = "error" not in result
            trace.append({"tool": name, "arguments": args if isinstance(args, dict) else {},
                          "result": result, "success": success})
            messages.append({"role": "tool", "tool_call_id": call["id"],
                             "content": json.dumps(result, ensure_ascii=False, default=str)})
    raise RuntimeError("模型工具调用轮数超过上限，已停止。")


def configured_key(secrets=None) -> str:
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    if not key and secrets is not None:
        try:
            key = secrets.get("DEEPSEEK_API_KEY", "")
        except (FileNotFoundError, KeyError):
            pass
    return key
