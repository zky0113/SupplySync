"""Validated input, forecast, simulation and tool-agent workbench."""
from __future__ import annotations

import html
import json

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .charts import network_chart, inventory_by_tier, history_chart, weekday_chart, terminal_chart, style
from .provider import Bundle, TYPE_LABELS, TYPE_COLORS, scenario_events
from .engine import (context, forecast_rows, holdout_score, initial_plan,
                     paired_experiment, public_result, daily_rows)
from .agent_service import configured_key, evidence_answer, live_answer

CHART_CONFIG = {"displayModeBar": False, "scrollZoom": False, "locale": "zh-CN"}


def heading(title, copy=""):
    st.markdown(f'<div class="section-title">{html.escape(title)}</div>'
                f'<div class="section-copy">{html.escape(copy)}</div>', unsafe_allow_html=True)


def card(label, value, unit="", foot="", pending=False):
    css = "stat-card pending" if pending else "stat-card"
    st.markdown(f'<div class="{css}"><div class="stat-label">{html.escape(label)}</div>'
                f'<div class="stat-value">{html.escape(str(value))}<span>{html.escape(unit)}</span></div>'
                f'<div class="stat-foot">{html.escape(foot)}</div></div>', unsafe_allow_html=True)


def empty(title, text):
    st.markdown(f'<div class="empty-state"><h3>{html.escape(title)}</h3>'
                f'<p>{html.escape(text)}</p></div>', unsafe_allow_html=True)


def plot(figure, key):
    st.plotly_chart(figure, width="stretch", config=CHART_CONFIG, key=key)


def network_view(bundle: Bundle, selected: str):
    left, right = st.columns([2.1, 1], gap="medium")
    with left, st.container(border=True):
        heading("四级供应链网络", "左侧选择节点可高亮其服务分支；悬停查看初始库存和节点能力。")
        legend = "".join(f'<span><i class="dot" style="background:{TYPE_COLORS[kind]}"></i>{label}</span>'
                         for kind, label in TYPE_LABELS.items())
        st.markdown(f'<div class="layer-legend">{legend}</div>', unsafe_allow_html=True)
        plot(network_chart(bundle, selected), "network_topology")
        st.caption("连线数字为基础运输提前期。该图展示网络配置与初始状态。")
    with right, st.container(border=True):
        state = bundle.initial_state(selected)
        heading(f"{selected} · {TYPE_LABELS[state['node_type']]}", f"初始状态日期 · {state['state_date']}")
        columns = st.columns(2)
        columns[0].metric("现货库存", f"{state['on_hand_units']:,} 件")
        columns[1].metric("存储容量", f"{state['storage_capacity_units']:,} 件")
        columns = st.columns(2)
        columns[0].metric("在途入站", f"{state['in_transit_inbound_units']:,} 件")
        columns[1].metric("欠下游量", f"{state['backorder_units']:,} 件")
        utilization = state["on_hand_units"] / state["storage_capacity_units"]
        st.progress(utilization, text=f"初始仓容占用 {utilization:.1%}")
        st.caption(f"每日处理能力 {state['daily_processing_capacity_units']:,} 件"
                   + (f" · 生产能力 {state['daily_production_capacity_units']:,} 件/天" if state["node_type"] == "supplier" else ""))
        st.divider()
        parent = state["upstream_node_id"] or "外部供给"
        st.write(f"**上游**　{parent}")
        children = state["downstream_node_ids"].replace("|", "、") or "面向消费者"
        st.write(f"**下游**　{children}")
        st.caption(f"服务终端：{'、'.join(bundle.descendants(selected))}")
        st.caption("已下单未发货量尚无独立输入字段；将在订单服务接入后显示。")
    lower_left, lower_right = st.columns([1, 1.65], gap="medium")
    with lower_left, st.container(border=True):
        heading("各层初始库存", "按节点层级求和，浅色部分为空余仓容。")
        plot(inventory_by_tier(bundle), "tier_inventory")
    with lower_right, st.container(border=True):
        heading("当前节点的运输约束", "静态配置；运输延迟场景不会改变这里的基础提前期。")
        frame = pd.DataFrame([edge for edge in bundle.edges
                              if selected in (edge["from_node"], edge["to_node"])])
        columns = ["from_node", "to_node", "base_lead_time_days", "transport_capacity_units_per_day",
                   "minimum_order_units", "order_multiple_units"]
        frame = frame[columns].rename(columns={"from_node": "发货节点", "to_node": "收货节点",
            "base_lead_time_days": "提前期 / 天", "transport_capacity_units_per_day": "运力 / 件每天",
            "minimum_order_units": "起订量 / 件", "order_multiple_units": "整批单位 / 件"})
        st.dataframe(frame, hide_index=True, width="stretch")
    with st.expander("查看完整节点与初始库存表"):
        frame = bundle.nodes.merge(bundle.inventory, on=["node_id", "sku_id"], validate="one_to_one")
        st.dataframe(frame, hide_index=True, width="stretch")
        st.download_button("下载初始库存 CSV", (bundle.root / "data" / "initial_inventory.csv").read_bytes(),
                           file_name="initial_inventory.csv", mime="text/csv", key="inventory_download")


def history_view(bundle: Bundle, selected: str):
    first, last = bundle.history["date"].min().date(), bundle.history["date"].max().date()
    heading("历史需求洞察", f"{selected} 服务分支的终端消费需求 · 上游曲线是终端需求汇总，不是上游订单。")
    control, view = st.columns([3, 1])
    with control:
        date_range = st.date_input("历史区间", value=(first, last), min_value=first, max_value=last,
                                   key="history_date_range", format="YYYY-MM-DD")
    with view:
        st.write("")
        show_terminals = st.toggle("展开终端曲线", value=False, key="history_show_terminals")
    if len(date_range) != 2:
        st.info("请选择区间结束日期。")
        return
    start, end = date_range
    if start > end:
        st.error("开始日期不能晚于结束日期。")
        return
    daily = bundle.daily_history(selected, start, end)
    if daily.empty:
        st.info("当前区间没有历史需求记录。")
        return
    cells = st.columns(3)
    with cells[0]:
        card("区间日均需求", f"{daily['demand_units'].mean():,.1f}", "件 / 天", f"{len(daily)} 个完整历史日")
    with cells[1]:
        card("区间需求范围", f"{daily['demand_units'].min():,}–{daily['demand_units'].max():,}", "件 / 天", "每日终端消费需求的最小值与最大值")
    with cells[2]:
        card("区间需求合计", f"{daily['demand_units'].sum():,}", "件", f"覆盖 {len(bundle.descendants(selected))} 个终端")
    st.write("")
    with st.container(border=True):
        plot(history_chart(bundle, selected, start, end, show_terminals), "history_demand")
        st.caption("黄色区域为该分支已知促销；虚线是回看历史的 7 日移动均值。促销倍率来自输入配置，图中不推断因果增量。")
    left, right = st.columns(2)
    with left, st.container(border=True):
        heading("周内需求规律", "当前区间的分星期样本均值，包含促销与随机波动。")
        plot(weekday_chart(daily), "weekday_pattern")
    with right, st.container(border=True):
        heading("终端需求构成", "同一区间，各终端实际合成历史需求的合计。")
        plot(terminal_chart(bundle, selected, start, end), "terminal_contribution")
    with st.expander("查看历史明细、促销记录与数据下载"):
        terminals = bundle.descendants(selected)
        rows = bundle.history[bundle.history["terminal_id"].isin(terminals)]
        rows = rows[(rows["date"] >= pd.Timestamp(start)) & (rows["date"] <= pd.Timestamp(end))].copy()
        rows["date"] = rows["date"].dt.strftime("%Y-%m-%d")
        st.dataframe(rows, hide_index=True, width="stretch")
        st.download_button("下载当前分支历史需求 CSV", rows.to_csv(index=False).encode("utf-8-sig"),
                           file_name=f"history_{selected}_{start}_{end}.csv", mime="text/csv", key="history_download")
        promotions = []
        for item in bundle.config["demand"]["promotions"]:
            targets = item["terminal_ids"]
            promotions.append({"促销 ID": item["id"], "开始日期": item["start_date"],
                               "结束日期": item["end_date"],
                               "终端范围": "全部终端" if targets == "all" else "、".join(targets),
                               "配置增量": f"{item['lift']:.0%}"})
        st.dataframe(promotions, hide_index=True, width="stretch")
    ctx = context(bundle)
    st.divider()
    heading("30 天需求预测", "只用 210 天历史与已知日历；预测曲线不包含仿真未来真值。")
    forecast = pd.DataFrame(forecast_rows(ctx, selected))
    observed = bundle.daily_history(selected).tail(28)
    figure = go.Figure()
    figure.add_scatter(x=observed["date"], y=observed["demand_units"], name="最近 28 天历史需求",
                       line=dict(color="#8D99B1", width=2))
    figure.add_scatter(x=forecast["date"], y=forecast["predicted_units"], name="未来 30 天预测",
                       line=dict(color="#6554D9", width=2.5))
    style(figure, height=340)
    figure.update_yaxes(title="件 / 天", rangemode="tozero")
    plot(figure, "forecast_chart")
    score = holdout_score(ctx, selected)
    score_cells = st.columns(3)
    score_cells[0].metric("28 天留出 WAPE", f"{score['model_wape']:.1%}")
    score_cells[1].metric("朴素基线 WAPE", f"{score['naive_wape']:.1%}")
    score_cells[2].metric("留出期模型 MAE", f"{score['model_mae']:.1f} 件/天")
    st.caption(f"验证区间从 {score['holdout_start']} 起，共 {score['holdout_days']} 天；"
               "训练仅用前 182 天。朴素基线重复训练期最后一周同星期需求。"
               "该评估是单次留出，不是滚动回测，也不能证明企业真实效果。")
    heading("首日补货建议", "请求量按初始现货、预测、安全库存、仓容、起订量与整批约束算出；供应商节点无上游采购单。")
    plans = [initial_plan(ctx, pid, selected) for pid in ("S1", "S2")]
    st.dataframe(pd.DataFrame([{"策略": p["policy"], "节点": p["node_id"],
        "向上游": p["upstream"] or "无", "建议下单 / 件": p["requested_units"],
        "初始库存位置 / 件": p["inventory_position"], "仓容 / 件": p["capacity"],
        "基础提前期 / 天": p["lead_days"], "起订量 / 件": p["minimum_order"],
        "整批单位 / 件": p["order_multiple"]} for p in plans]),
        hide_index=True, width="stretch")
    st.caption("这是第 1 天事前计划，不是实际发货/到货；S1 后续按观察到的下游订单调整，S2 汇总终端需求预测。")
    st.download_button("下载当前节点预测 CSV", forecast.to_csv(index=False).encode("utf-8-sig"),
                       file_name=f"forecast_{selected}.csv", mime="text/csv", key="forecast_download")


def simulation_view(bundle: Bundle, scenario_id: str, selected: str):
    heading("分散与协同策略对照", "同初始库存、同场景需求、同成本参数；显示最新计算与逐日审计通过后的结果。")
    if st.button("运行 30 天双策略对照", type="primary", width="stretch", key="simulation_run"):
        with st.spinner("正在仿真并逐日核验库存、订单账本与成本…"):
            st.session_state["paired_result"] = paired_experiment(context(bundle), scenario_id)
    experiment = st.session_state.get("paired_result")
    if experiment and experiment["scenario_id"] != scenario_id:
        st.info("场景已切换。请重新运行对照实验，避免把旧场景结果当成当前场景。")
        experiment = None
    if experiment:
        left, right = experiment["S1"]["metrics"], experiment["S2"]["metrics"]
        cards = st.columns(4)
        cards[0].metric("S1 总成本", f"¥{left['total_cost']:,.0f}")
        cards[1].metric("S2 总成本", f"¥{right['total_cost']:,.0f}")
        diff = right['total_cost'] - left['total_cost']
        if diff < 0:
            cards[1].markdown(":green[下降]")
        elif diff > 0:
            cards[1].markdown(":red[上升]")
        cards[2].metric("终端缺货率 S1 → S2", f"{left['stockout_rate']:.2%} → {right['stockout_rate']:.2%}")
        cards[3].metric("周转次数 S1 → S2", f"{left['inventory_turnover']:.2f} → {right['inventory_turnover']:.2f}")
        if right["total_cost"] > left["total_cost"]:
            st.warning("本场景下协同策略总成本更高；页面保留真实对照结果，不隐藏不利场景。")
        st.caption(f"运行编号 {experiment['run_id']} · 引擎签名 {experiment['engine_fingerprint']} · "
                   "无 warm-up · 双策略逐日需求相同 · 容量、订单账本、终端失销和成本审计通过。")
        costs = pd.DataFrame({"成本": ["采购", "持有", "运输", "下单固定", "缺货惩罚"],
            "S1": [left[k] for k in ("cost_procurement", "cost_holding", "cost_transport", "cost_order_fixed", "cost_shortage")],
            "S2": [right[k] for k in ("cost_procurement", "cost_holding", "cost_transport", "cost_order_fixed", "cost_shortage")]})
        left_col, right_col = st.columns([1, 1.4])
        with left_col, st.container(border=True):
            heading("成本拆解", "总成本差异由各分项逐日加总得出。")
            fig = go.Figure()
            for pid, color in (("S1", "#8D99B1"), ("S2", "#6554D9")):
                fig.add_bar(name=pid, x=costs["成本"], y=costs[pid], marker_color=color)
            style(fig, height=300)
            fig.update_layout(barmode="group")
            plot(fig, "simulation_cost")
        with right_col, st.container(border=True):
            heading(f"{selected} 逐日现货", "日终库存；这里只显示已经记录的状态，不推断在途量。")
            daily = pd.DataFrame(daily_rows(experiment, selected))
            fig = go.Figure()
            for pid, color in (("S1", "#8D99B1"), ("S2", "#6554D9")):
                part = daily[daily["policy"] == pid]
                fig.add_scatter(x=part["date"], y=part["node_on_hand"], name=pid,
                                mode="lines+markers", marker=dict(size=4), line=dict(color=color))
            style(fig, height=300)
            plot(fig, "simulation_inventory")
        with st.container(border=True):
            heading("逐日终端失销与牛鞭", "真实仿真日志汇总；牛鞭为各层订单方差 / 终端消费需求方差。")
            fig = go.Figure()
            for pid, color in (("S1", "#8D99B1"), ("S2", "#D89B39")):
                part = daily[daily["policy"] == pid]
                fig.add_scatter(x=part["date"], y=part["terminal_lost_sales"], name=f"{pid} 失销",
                                mode="lines", line=dict(color=color))
            style(fig, height=220)
            plot(fig, "simulation_lost_sales")
            st.dataframe(pd.DataFrame([{"策略": pid, "终端层": experiment[pid]["metrics"]["bullwhip_by_tier"]["terminal"],
                "城市层": experiment[pid]["metrics"]["bullwhip_by_tier"]["city"],
                "区域层": experiment[pid]["metrics"]["bullwhip_by_tier"]["regional"]}
                for pid in ("S1", "S2")]), hide_index=True, width="stretch")
        with st.expander("逐日结果与可复算证据"):
            st.dataframe(daily, hide_index=True, width="stretch")
            st.download_button("下载逐日记录 CSV", daily.to_csv(index=False).encode("utf-8-sig"),
                               file_name=f"daily_{scenario_id}_{experiment['run_id']}.csv", mime="text/csv",
                               key="daily_download")
            st.download_button("下载指标及运行元数据 JSON", json.dumps(public_result(experiment), ensure_ascii=False, indent=2).encode(),
                               file_name=f"result_{scenario_id}_{experiment['run_id']}.json", mime="application/json",
                               key="metrics_download")
    else:
        empty("等待运行对照实验", "点击上方按钮后实时运行最新计算版本；不会读取旧实验报告。")
    with st.container(border=True):
        heading("实验场景条件预览", "这是实验设计者视图，仅展示已校验的外生条件，不向未来补货决策暴露未发生扰动。")
        st.caption(f"仿真起点 {bundle.scenarios['simulation_start_date']} · 配置窗口 {bundle.scenarios['horizon_days']} 天")
        events = scenario_events(bundle, scenario_id)
        if events.empty:
            st.info("基准场景没有额外扰动，保留已知促销与普通随机需求。")
        else:
            st.dataframe(events, hide_index=True, width="stretch")
        st.caption("场景扰动只由仿真器实现；补货策略不预知未来真实需求和未发生扰动。")
    with st.expander("指标定义与实验边界"):
        st.markdown("- 总成本：外部采购 + 持有 + 运输 + 下单 + 终端失销惩罚。\n"
                    "- 终端缺货率：终端失销量 ÷ 终端消费需求量。\n"
                    "- 窗口周转次数：窗口终端实际销量 ÷ 全系统日终平均现货。\n"
                    "- 牛鞭系数：各层每日下单总量方差 ÷ 每日终端需求总量方差。\n"
                    "- 当前是固定 30 天单 SKU 合成实验，无真实企业数据和多随机种子置信区间。")


def agent_view(bundle: Bundle, selected: str, scenario_id: str):
    heading("Agent 分析与证据", "四个只读业务工具：库存、预测、首日补货和策略对照；可逐项核对调用证据。")
    st.markdown('<div class="flow"><span>读取授权状态</span><span>调用需求预测</span>'
                '<span>计算补货计划</span><span>运行对照实验</span><span>解释与证据</span></div>', unsafe_allow_html=True)
    question = st.text_area("业务问题", value=f"{selected} 的补货建议依据是什么？在 {scenario_id} 场景协同是否更好？",
                            max_chars=1500, key="agent_question")
    key = configured_key(st.secrets)
    mode = st.radio("分析模式", ["证据模式（离线可用）", "DeepSeek 工具调用"], horizontal=True,
                    key="agent_mode", disabled=False)
    if mode.startswith("DeepSeek") and not key:
        st.info("未配置 DEEPSEEK_API_KEY。请在服务端环境变量或 .streamlit/secrets.toml 设置；"
                "不要把密钥写入前端、仓库或聊天。可切回离线证据模式。")
    if st.button("调用业务工具并分析", type="primary", width="stretch", key="agent_run",
                 disabled=mode.startswith("DeepSeek") and not key):
        try:
            with st.spinner("正在调用工具并整理证据…"):
                ctx = context(bundle)
                answer = (live_answer(ctx, question, selected, scenario_id, key)
                          if mode.startswith("DeepSeek") else evidence_answer(ctx, question, selected, scenario_id))
            st.session_state["agent_response"] = {**answer, "node_id": selected, "scenario_id": scenario_id}
        except Exception as error:
            st.session_state.pop("agent_response", None)
            st.error(f"分析未完成：{error}")
    answer = st.session_state.get("agent_response")
    if answer and (answer["node_id"], answer["scenario_id"]) != (selected, scenario_id):
        st.info("节点或场景已改变，请重新分析。")
        answer = None
    if answer:
        st.success(answer["mode"] + (f" · 运行 {answer['run_id']}" if answer["run_id"] else ""))
        st.write(answer["answer"])
        if answer["mode"].startswith("DeepSeek"):
            st.caption("模型解释可能有误，请以展开的工具结果和仿真页指标为准。")
        with st.expander(f"查看实际工具调用与结果（{len(answer['trace'])} 次）", expanded=True):
            for index, step in enumerate(answer["trace"], 1):
                st.markdown(f"**{index}. {step['tool']}**　`{json.dumps(step['arguments'], ensure_ascii=False)}`")
                st.json(step["result"])
        st.download_button("下载 Agent 调用证据 JSON", json.dumps(answer, ensure_ascii=False, indent=2).encode(),
                           file_name=f"agent_trace_{selected}_{scenario_id}.json", mime="application/json",
                           key="agent_trace_download")
    with st.container(border=True):
        heading("当前已验证的输入证据", "数据校验只证明合成输入一致性；仿真运行另做状态和账本校验。")
        for check in bundle.report["checks"]:
            st.markdown(f"✓ {check}")
        st.caption(f"快照 ID：{bundle.snapshot_id} · seed：{bundle.report['seed']}")
        st.download_button("下载输入校验报告", json.dumps(bundle.report, ensure_ascii=False, indent=2).encode("utf-8"),
                           file_name="validation_report.json", mime="application/json", key="report_download")
