"""SupplySync: validated input, forecasting, replenishment and Agent workbench."""
from pathlib import Path
import base64
import html
import json

import streamlit as st

from demo.provider import load_bundle, TYPE_LABELS, SCENARIO_LABELS
from demo.engine import context
from demo.views import network_view, history_view, simulation_view, agent_view, card

ROOT = Path(__file__).resolve().parent
ROUTE_MOTIF = (ROOT / "assets" / "supply_route.svg").read_text(encoding="utf-8")
PARTNER_LOGO = "data:image/jpeg;base64," + base64.b64encode(
    (ROOT / "assets" / "shulian-tech-logo.jpg").read_bytes()).decode("ascii")
st.set_page_config(page_title="SupplySync · 多级库存协同决策台", page_icon="◈", layout="wide",
                   initial_sidebar_state="expanded")
st.markdown(f"<style>{(ROOT / 'assets' / 'styles.css').read_text(encoding='utf-8')}</style>", unsafe_allow_html=True)

try:
    bundle = load_bundle(ROOT)
    engine_ctx = context(bundle)
except Exception as error:
    st.error(f"输入或计算版本校验失败，页面已停止展示：{error}")
    st.info("请核查快照和计算版本；不要直接修改校验清单绕过检查。")
    st.stop()

with st.sidebar:
    st.markdown('<div class="brand"><div class="brand-mark">S</div><div><div class="brand-name">SupplySync</div>'
                '<div class="brand-sub">SUPPLY CHAIN WORKBENCH</div></div></div>', unsafe_allow_html=True)
    st.markdown(f'<div class="partner-lockup"><img src="{PARTNER_LOGO}" alt="数联科技标志">'
                '<div><span>赛事企业标识</span><strong>数联科技</strong></div></div>',
                unsafe_allow_html=True)
    st.markdown('<span class="badge green">输入与计算版本已校验</span> <span class="badge">可运行 Demo</span>', unsafe_allow_html=True)
    st.markdown('<div class="origin-note">数联科技 × 华东师范大学赛事项目原型<br>SupplySync 为参赛作品名称，非企业官方产品。</div>',
                unsafe_allow_html=True)
    st.markdown('<div class="side-kicker">查看范围</div>', unsafe_allow_html=True)
    node_ids = bundle.nodes["node_id"].tolist()
    selected = st.selectbox("供应链节点", node_ids, key="selected_node",
                            format_func=lambda node_id: f"{node_id} · {TYPE_LABELS[bundle.node(node_id)['node_type']]}")
    st.caption("节点选择同步作用于网络、历史、预测、计划、仿真轨迹和 Agent。")
    st.markdown('<div class="side-kicker">实验设置</div>', unsafe_allow_html=True)
    scenario_ids = [item["id"] for item in bundle.scenarios["scenarios"]]
    scenario_id = st.selectbox("场景条件", scenario_ids, key="scenario_id",
                              format_func=lambda value: SCENARIO_LABELS.get(value, value))
    st.caption(f"仿真窗口：{bundle.scenarios['horizon_days']} 天；双策略同初态与同需求。运行按钮位于『仿真对比』。")
    st.markdown('<div class="side-kicker">数据版本</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="side-detail">单 SKU 合成网络 · {html.escape(bundle.config["sku_id"])}<br>'
                f'历史：{bundle.report["summary"]["history_start_date"]} → {bundle.history["date"].max():%Y-%m-%d}<br>'
                f'seed：{bundle.report["seed"]}<br>快照：{bundle.snapshot_id}<br>'
                f'引擎：{engine_ctx.fingerprint}</div>', unsafe_allow_html=True)
    st.markdown('<div class="side-kicker">功能状态</div>', unsafe_allow_html=True)
    st.caption("✓ 网络与初始库存\n\n✓ 历史需求与统计\n\n✓ 预测与首日补货\n\n✓ 双策略仿真对比\n\n✓ Agent 工具证据（模型模式需密钥）")
    st.download_button("下载输入校验报告", json.dumps(bundle.report, ensure_ascii=False, indent=2).encode("utf-8"),
                       file_name="validation_report.json", mime="application/json", width="stretch", key="sidebar_report_download")

st.markdown('<div class="hero-surface"><div class="hero-layout"><div class="hero-text">'
            '<div class="eyebrow">赛题二 B · 供应链多级库存智能协同与补货决策</div>'
            '<div class="hero-row"><h1>多级库存协同决策台</h1><span class="hero-badge">输入数据已验证</span></div>'
            '<p class="hero-copy">从历史需求预测、首日补货到 30 天双策略仿真与工具 Agent，形成可复算的供应链决策闭环。</p>'
            '<div class="hero-caption">供应商 → 区域仓 → 城市仓 → 终端</div></div>'
            f'<div class="hero-art"><div class="hero-partner"><img src="{PARTNER_LOGO}" alt="数联科技标志">'
            '<span>数联科技 × 华东师范大学</span></div>'
            f'{ROUTE_MOTIF}</div></div></div>', unsafe_allow_html=True)
st.markdown('<div class="input-note">◈　合成数据演示：预测与策略效果均由当前固定版本计算；不等同真实企业收益。切换场景后请重新运行仿真。</div>', unsafe_allow_html=True)

cells = st.columns(3)
with cells[0]:
    card("完整供应链网络", len(bundle.nodes), "个节点", f"4 级结构 · {len(bundle.edges)} 条运输线路")
with cells[1]:
    card("历史需求覆盖", bundle.report["summary"]["history_days"], "天", f"8 个终端 · {len(bundle.history):,} 条终端日记录")
with cells[2]:
    card("全网初始现货", f"{bundle.inventory['on_hand_units'].sum():,}", "件", f"状态日期 {bundle.inventory['state_date'].iloc[0]}")

tabs = st.tabs(["网络与库存", "需求与预测", "仿真对比", "Agent 与证据"])
with tabs[0]:
    network_view(bundle, selected)
with tabs[1]:
    history_view(bundle, selected)
with tabs[2]:
    simulation_view(bundle, scenario_id, selected)
with tabs[3]:
    agent_view(bundle, selected, scenario_id)

with st.expander("数据依据、校验范围与当前版本边界"):
    st.write("来源：队友赛事项目的合成输入。展示快照保留原始字节并在页面每次运行时核验。")
    st.write("校验覆盖字段、关系、日期、数量、容量、成本、促销、场景以及 seed 复现。合成数据用于开发和对照实验，未经企业真实业务校准。")
    if bundle.report["missing_source_metadata"]:
        st.caption("原资料缺少以下元数据：" + "、".join(bundle.report["missing_source_metadata"])
                   + "。本项目的校验报告为独立重验记录，未冒充原始生成清单。")
    st.caption("future_demand.csv 仅位于计算输入，仿真器用于生成真实需求；预测和策略接口不接收它。无 warm-up 的固定 30 天窗口。")
st.markdown('<div class="footer-note">SupplySync · 可复现的合成数据工作台　｜　输入快照、计算引擎与每次对照结果均可追溯</div>', unsafe_allow_html=True)
