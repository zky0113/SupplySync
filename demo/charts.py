"""Plotly views of validated inputs; all calculations are retrospective."""
from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

from .provider import Bundle, TYPE_COLORS, TYPE_LABELS

FONT = "Segoe UI, Microsoft YaHei, PingFang SC, sans-serif"
PURPLE = "#6554D9"
BLUE = "#4687D8"


def style(figure: go.Figure, *, height=340, margin=None) -> go.Figure:
    figure.update_layout(
        height=height, paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family=FONT, size=12, color="#68728B"),
        margin=margin or dict(l=30, r=15, t=20, b=40),
        hoverlabel=dict(bgcolor="white", font=dict(family=FONT, color="#20263B")),
        legend=dict(orientation="h", y=1.1, x=0),
    )
    figure.update_xaxes(showgrid=False, zeroline=False)
    figure.update_yaxes(gridcolor="#EBEDF5", zeroline=False)
    return figure


def network_chart(bundle: Bundle, selected: str) -> go.Figure:
    terminals = sorted(bundle.nodes[bundle.nodes["node_type"] == "terminal"]["node_id"])
    terminal_y = {node_id: float(len(terminals) - 1 - index) for index, node_id in enumerate(terminals)}
    coordinates = {}
    for row in bundle.nodes.to_dict("records"):
        downstream = bundle.descendants(row["node_id"])
        coordinates[row["node_id"]] = (row["tier"], sum(terminal_y[item] for item in downstream) / len(downstream))
    related = bundle.related_nodes(selected)
    figure = go.Figure()
    for edge in bundle.edges:
        left, right = edge["from_node"], edge["to_node"]
        x0, y0 = coordinates[left]
        x1, y1 = coordinates[right]
        active = left in related and right in related
        figure.add_trace(go.Scatter(
            x=[x0, x0 + .44, x1 - .44, x1], y=[y0, y0, y1, y1], mode="lines",
            line=dict(color="#B7B0EB" if active else "#E4E7F0", width=2.5 if active else 1.3),
            hoverinfo="skip", showlegend=False,
        ))
        if active:
            figure.add_annotation(x=(x0+x1)/2, y=(y0+y1)/2,
                                  text=f"{edge['base_lead_time_days']} 天", showarrow=False,
                                  font=dict(size=10, color="#8B82BA"), bgcolor="#F7F8FC")
    for node_type in TYPE_LABELS:
        rows = bundle.nodes[bundle.nodes["node_type"] == node_type].to_dict("records")
        figure.add_trace(go.Scatter(
            x=[coordinates[row["node_id"]][0] for row in rows],
            y=[coordinates[row["node_id"]][1] for row in rows],
            mode="markers+text", text=[row["node_id"] for row in rows], textposition="middle center",
            textfont=dict(size=11, color="white"),
            marker=dict(size=[46 if row["node_id"] == selected else 36 for row in rows],
                        color=TYPE_COLORS[node_type],
                        opacity=[1 if row["node_id"] in related else .35 for row in rows],
                        line=dict(color="white", width=3)),
            customdata=[[TYPE_LABELS[node_type], row["initial_on_hand_units"],
                         row["storage_capacity_units"], row["daily_processing_capacity_units"]] for row in rows],
            hovertemplate="<b>%{text} · %{customdata[0]}</b><br>初始现货：%{customdata[1]} 件"
                          "<br>仓容：%{customdata[2]} 件<br>处理能力：%{customdata[3]} 件/天<extra></extra>",
            showlegend=False,
        ))
    for tier, label in enumerate(TYPE_LABELS.values()):
        figure.add_annotation(x=tier, y=len(terminals) - .22, text=f"<b>{label}</b>", showarrow=False,
                              font=dict(size=13, color="#4F5972"))
    style(figure, height=440, margin=dict(l=28, r=28, t=25, b=12))
    figure.update_xaxes(visible=False, range=[-.25, 3.25], fixedrange=True)
    figure.update_yaxes(visible=False, range=[-.55, len(terminals)+.25], fixedrange=True)
    figure.update_layout(hovermode="closest")
    return figure


def inventory_by_tier(bundle: Bundle) -> go.Figure:
    rows = bundle.nodes.groupby("node_type").agg(
        on_hand=("initial_on_hand_units", "sum"), capacity=("storage_capacity_units", "sum"))
    kinds = list(TYPE_LABELS)
    figure = go.Figure()
    figure.add_bar(name="初始现货", x=[TYPE_LABELS[kind] for kind in kinds],
                   y=[int(rows.loc[kind, "on_hand"]) for kind in kinds],
                   marker_color=[TYPE_COLORS[kind] for kind in kinds],
                   hovertemplate="%{x}<br>初始现货：%{y:,} 件<extra></extra>")
    figure.add_bar(name="空余仓容", x=[TYPE_LABELS[kind] for kind in kinds],
                   y=[int(rows.loc[kind, "capacity"] - rows.loc[kind, "on_hand"]) for kind in kinds],
                   marker_color="#E9ECF4", hovertemplate="%{x}<br>空余仓容：%{y:,} 件<extra></extra>")
    style(figure, height=270)
    figure.update_layout(barmode="stack")
    figure.update_yaxes(title="件")
    return figure


def history_chart(bundle: Bundle, node_id: str, start, end, show_terminals=False) -> go.Figure:
    figure = go.Figure()
    daily = bundle.daily_history(node_id, start, end)
    if show_terminals:
        subset = bundle.history[bundle.history["terminal_id"].isin(bundle.descendants(node_id))]
        subset = subset[(subset["date"] >= pd.Timestamp(start)) & (subset["date"] <= pd.Timestamp(end))]
        for terminal_id, rows in subset.groupby("terminal_id"):
            figure.add_trace(go.Scatter(x=rows["date"], y=rows["demand_units"], name=terminal_id,
                                       mode="lines", line=dict(width=1.5)))
    else:
        figure.add_trace(go.Scatter(
            x=daily["date"], y=daily["demand_units"], name="历史消费需求",
            mode="lines", line=dict(color=PURPLE, width=2.1),
            fill="tozeroy", fillcolor="rgba(101,84,217,.055)",
            hovertemplate="%{x|%Y-%m-%d}<br>消费需求：%{y:,} 件<extra></extra>",
        ))
        figure.add_trace(go.Scatter(
            x=daily["date"], y=daily["moving_average_7d"], name="7 日移动均值（历史统计）",
            line=dict(color=BLUE, width=2, dash="dot"),
            hovertemplate="%{x|%Y-%m-%d}<br>7 日均值：%{y:.1f} 件<extra></extra>",
        ))
    terminals = set(bundle.descendants(node_id))
    for promotion in bundle.config["demand"]["promotions"]:
        targets = terminals if promotion["terminal_ids"] == "all" else terminals & set(promotion["terminal_ids"])
        first = max(pd.Timestamp(promotion["start_date"]), pd.Timestamp(start))
        last = min(pd.Timestamp(promotion["end_date"]), pd.Timestamp(end))
        if targets and first <= last:
            figure.add_shape(type="rect", xref="x", yref="paper", x0=first,
                             x1=last+pd.Timedelta(days=1), y0=0, y1=1,
                             fillcolor="rgba(216,155,57,.12)", line_width=0, layer="below")
            figure.add_annotation(x=first+(last-first)/2, y=1, yref="paper", text="促销",
                                  showarrow=False, font=dict(color="#B18029", size=11))
    style(figure, height=370)
    figure.update_layout(hovermode="x unified")
    figure.update_xaxes(title="历史日期")
    figure.update_yaxes(title="消费需求（件 / 天）", rangemode="tozero")
    return figure


def weekday_chart(daily: pd.DataFrame) -> go.Figure:
    frame = daily.assign(weekday=daily["date"].dt.weekday)
    averages = frame.groupby("weekday")["demand_units"].mean()
    days = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    figure = go.Figure(go.Bar(
        x=[days[index] for index in averages.index], y=averages.values,
        marker_color=[PURPLE if index >= 5 else "#ACA2EB" for index in averages.index],
        hovertemplate="%{x}<br>样本平均需求：%{y:.1f} 件<extra></extra>"))
    style(figure, height=250)
    figure.update_yaxes(title="件 / 天")
    return figure


def terminal_chart(bundle: Bundle, node_id: str, start, end) -> go.Figure:
    frame = bundle.history[bundle.history["terminal_id"].isin(bundle.descendants(node_id))]
    frame = frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]
    totals = frame.groupby("terminal_id")["demand_units"].sum().sort_index()
    figure = go.Figure(go.Bar(x=totals.index, y=totals.values, marker_color="#4687D8",
                             hovertemplate="%{x}<br>区间需求合计：%{y:,} 件<extra></extra>"))
    style(figure, height=250)
    figure.update_yaxes(title="区间需求（件）")
    return figure
