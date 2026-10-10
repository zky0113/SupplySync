"""Read validated input snapshots; never import agent.py or experiment results."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sys
import tempfile

import pandas as pd

TYPE_LABELS = {
    "supplier": "供应商", "regional_warehouse": "区域仓",
    "city_warehouse": "城市仓", "terminal": "终端",
}
TYPE_COLORS = {
    "supplier": "#6554D9", "regional_warehouse": "#4687D8",
    "city_warehouse": "#29A38C", "terminal": "#D89B39",
}
SCENARIO_LABELS = {
    "baseline": "基准场景", "demand_surge": "东部需求激增",
    "demand_decline": "西部需求下降", "transport_delay": "运输延迟",
    "supply_limit": "供应受限", "combined_stress": "组合压力",
}
CAPABILITIES = {
    "network": True, "initial_inventory": True, "historical_demand": True,
    "forecast": True, "replenishment_plan": True,
    "validated_simulation": True, "agent": True,
}


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


EMBEDDED_VALIDATION_REPORT = r'''{
  "schema_version": "frontend-snapshot-1.0",
  "status": "passed",
  "snapshot_id": "e697ed76be457e7f",
  "checked_at_utc": "2026-10-08T03:52:28.323536+00:00",
  "source_project": "teammate_agent_project",
  "data_kind": "synthetic_single_sku",
  "seed": 20260928,
  "summary": {
    "schema_version": "1.0",
    "seed": 20260928,
    "sku_id": "SKU-001",
    "history_start_date": "2026-02-01",
    "history_days": 210,
    "future_start_date": "2026-08-30",
    "future_days": 30,
    "node_count": 15,
    "node_count_by_type": {
      "supplier": 1,
      "regional_warehouse": 2,
      "city_warehouse": 4,
      "terminal": 8
    },
    "edge_count": 14,
    "terminal_history_rows": 1680,
    "scenario_count": 6,
    "history_total_demand_units": 55532,
    "history_daily_total_mean_units": 264.44,
    "history_daily_total_std_units": 38.07,
    "history_daily_total_min_units": 191,
    "history_daily_total_max_units": 390,
    "history_mean_units_per_terminal_per_day": {
      "T01": 25.49,
      "T02": 30.03,
      "T03": 32.62,
      "T04": 27.52,
      "T05": 36.7,
      "T06": 40.49,
      "T07": 27.81,
      "T08": 43.78
    },
    "history_mean_total_units_by_weekday_monday_first": [
      236.23,
      240.5,
      253.4,
      257.63,
      273.27,
      303.13,
      286.9
    ],
    "initial_on_hand_units_by_type": {
      "supplier": 1238,
      "regional_warehouse": 1300,
      "city_warehouse": 998,
      "terminal": 757
    }
  },
  "file_sha256": {
    "data/nodes.csv": "ce5ddd323a9d6161f81f5b4993c57f9931d7a7a9ae4b0e60a6e2b197ac6ed4d2",
    "data/initial_inventory.csv": "52cf664b7fcd43739201cab0f5f91a7cb910601309b77490ac50d5d7beb7b1d9",
    "data/demand_history.csv": "75aa651d87fb05b8c2b4457439aae8d72513efa0ec862152ad962a03140de90b",
    "data/known_calendar.csv": "fa0424366709266e83b79a651db11d5607d2f04a35341278a340ff807794130c",
    "data/network_config.json": "62080212ed1d124c8fb793e87ac3d8bc570baea89d4319a32fda6a76a058dc1a",
    "data/cost_config.json": "890cefe0bac2bf129d038b9a96112cf230b929990fe0bfd1142a3f2bd6002deb",
    "data/scenario_config.json": "e3829bf34de7c2826d41f70841912c1f34f37b4d5955c2a6b54729e7cd023924",
    "data/source_config.json": "22357608fffd3b5bad7cb3af1c2b6f2cea2a2f2a2e9daea14a823d715200c49d",
    "vendor/supply_data/__init__.py": "1d76cebc92876697b9e01b5eeb91ffd6490a49100c174cdd9c2bc6271c5a644a",
    "vendor/supply_data/schema.py": "ae4617af4c4bed003c33cbf97e8d8ac0c446871cdf505644f8af63262ab2e0ff",
    "vendor/supply_data/io_utils.py": "560ae79745c4c17978f2f3c8800d4269d022ca1bfe15789254c41dab2dc9f11d",
    "vendor/supply_data/generator.py": "2a8d79cdf05b3da3d41674edabbeab5b94afdee8ab019be8522614a8b2049f7e",
    "vendor/supply_data/validation.py": "55f8cbf8476cbcdccba3bf797e42da4b49d8077aca7768926308b14cd2328ebf"
  },
  "source_input_sha256": {
    "nodes.csv": "ce5ddd323a9d6161f81f5b4993c57f9931d7a7a9ae4b0e60a6e2b197ac6ed4d2",
    "initial_inventory.csv": "52cf664b7fcd43739201cab0f5f91a7cb910601309b77490ac50d5d7beb7b1d9",
    "demand_history.csv": "75aa651d87fb05b8c2b4457439aae8d72513efa0ec862152ad962a03140de90b",
    "known_calendar.csv": "fa0424366709266e83b79a651db11d5607d2f04a35341278a340ff807794130c",
    "network_config.json": "62080212ed1d124c8fb793e87ac3d8bc570baea89d4319a32fda6a76a058dc1a",
    "cost_config.json": "890cefe0bac2bf129d038b9a96112cf230b929990fe0bfd1142a3f2bd6002deb",
    "scenario_config.json": "e3829bf34de7c2826d41f70841912c1f34f37b4d5955c2a6b54729e7cd023924",
    "future_demand.csv": "44890640d60be9e48721e8e24765c86021045b0fb157cec5a66e0d4c99d59797"
  },
  "missing_source_metadata": [
    "effective_config.json",
    "generation_manifest.json",
    "data_summary.json"
  ],
  "checks": [
    "配置结构、四级邻接关系与完整连通性",
    "节点/库存字段、SKU、状态日期与容量约束",
    "运输提前期、运力、起订量与整批约束",
    "历史日期连续、终端日组合唯一且完整、需求非负整数",
    "促销日历、星期、历史促销标记一致",
    "成本非负并与配置一致",
    "场景目标、日期、倍率与外生扰动规则",
    "原始八个输入文件与指定 seed 生成器逐字节一致",
    "同配置重复生成一致，展示快照和校验源码 SHA-256 固定"
  ],
  "scope": "Checks establish synthetic input consistency and reproduction, not real-world accuracy, forecast quality, simulator correctness or policy effectiveness.",
  "future_truth_in_display_bundle": false,
  "scenario_visibility": "Experiment designer preview only; not a future planner feed."
}'''


def validate_snapshot(root: Path) -> dict:
    report_path = root / "reports" / "validation_report.json"
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    else:
        report = json.loads(EMBEDDED_VALIDATION_REPORT)
    if report.get("status") != "passed" or report.get("future_truth_in_display_bundle") is not False:
        raise ValueError("展示快照未通过校验或包含未来真值。")
    expected_paths = {
        *(f"data/{name}" for name in (
            "nodes.csv", "initial_inventory.csv", "demand_history.csv", "known_calendar.csv",
            "network_config.json", "cost_config.json", "scenario_config.json", "source_config.json")),
        *(f"vendor/supply_data/{name}" for name in (
            "__init__.py", "schema.py", "io_utils.py", "generator.py", "validation.py")),
    }
    if set(report.get("file_sha256", {})) != expected_paths:
        raise ValueError("快照字段或文件清单不完整。")
    captured = {}
    for name, expected in report["file_sha256"].items():
        raw = (root / name).read_bytes()
        if sha256(raw) != expected:
            raise ValueError(f"数据或校验源码已发生变化：{name}。请重新审核并生成快照。")
        captured[name] = raw
    snapshot_id = sha256(json.dumps(report["file_sha256"], sort_keys=True).encode("utf-8"))[:16]
    if snapshot_id != report.get("snapshot_id"):
        raise ValueError("快照 ID 与文件签名不一致。")
    config = json.loads(captured["data/source_config.json"])
    if report.get("seed") != config["seed"]:
        raise ValueError("校验报告 seed 与配置不一致。")
    sys.path.insert(0, str(root / "vendor"))
    from supply_data.generator import generate_payloads
    from supply_data.validation import validate_config, validate_dataset
    validate_config(config)
    payloads = generate_payloads(config)
    if payloads != generate_payloads(config):
        raise ValueError("随机种子复现检查失败。")
    for name, raw in captured.items():
        if name.startswith("data/") and not name.endswith("source_config.json"):
            if raw != payloads[Path(name).name]:
                raise ValueError(f"输入与指定 seed 的生成结果不一致：{name}")
    # The full original validator needs future truth; regenerated bytes stay in a
    # temporary validation folder, never in Bundle, chart or downloadable UI data.
    with tempfile.TemporaryDirectory(prefix="supplysync-validation-") as temporary:
        directory = Path(temporary)
        for name, raw in payloads.items():
            (directory / name).write_bytes(raw)
        summary = validate_dataset(directory, config, verify_manifest=False)
    safe_summary = {key: value for key, value in summary.items()
                    if not key.startswith("future_demand") and key != "future_total_demand_units_by_scenario"}
    if safe_summary != report.get("summary"):
        raise ValueError("报告摘要与重新校验结果不一致。")
    return report


@dataclass
class Bundle:
    root: Path
    report: dict
    config: dict
    nodes: pd.DataFrame
    inventory: pd.DataFrame
    history: pd.DataFrame
    calendar: pd.DataFrame
    network: dict
    costs: dict
    scenarios: dict

    @property
    def snapshot_id(self) -> str:
        return self.report["snapshot_id"]

    @property
    def edges(self) -> list[dict]:
        return self.network["edges"]

    def node(self, node_id: str) -> dict:
        matches = self.nodes[self.nodes["node_id"] == node_id]
        if len(matches) != 1:
            raise ValueError(f"未知节点：{node_id}")
        return matches.iloc[0].to_dict()

    def descendants(self, node_id: str) -> list[str]:
        item = self.node(node_id)
        if item["node_type"] == "terminal":
            return [node_id]
        children = [child for child in item["downstream_node_ids"].split("|") if child]
        return sorted(terminal for child in children for terminal in self.descendants(child))

    def related_nodes(self, node_id: str) -> set[str]:
        related = {node_id}
        for terminal_id in self.descendants(node_id):
            current = terminal_id
            while current:
                related.add(current)
                current = self.node(current)["upstream_node_id"]
        return related

    def daily_history(self, node_id: str, start=None, end=None) -> pd.DataFrame:
        terminals = self.descendants(node_id)
        frame = self.history[self.history["terminal_id"].isin(terminals)]
        # Calculate the trailing statistic on the complete history first. A
        # display-range change must not discard the six preceding observations.
        result = frame.groupby("date", as_index=False)["demand_units"].sum().sort_values("date")
        result["moving_average_7d"] = result["demand_units"].rolling(7, min_periods=7).mean()
        if start is not None:
            result = result[result["date"] >= pd.Timestamp(start)]
        if end is not None:
            result = result[result["date"] <= pd.Timestamp(end)]
        return result.reset_index(drop=True)

    def initial_state(self, node_id: str) -> dict:
        node = self.node(node_id)
        inventory = self.inventory[self.inventory["node_id"] == node_id].iloc[0].to_dict()
        return {**node, **inventory}

    def scenario(self, scenario_id: str) -> dict:
        return next(item for item in self.scenarios["scenarios"] if item["id"] == scenario_id)


def load_bundle(root: Path) -> Bundle:
    report = validate_snapshot(root)
    directory = root / "data"
    def read_json(name):
        return json.loads((directory / name).read_text(encoding="utf-8"))
    nodes = pd.read_csv(directory / "nodes.csv", keep_default_na=False)
    inventory = pd.read_csv(directory / "initial_inventory.csv", keep_default_na=False)
    history = pd.read_csv(directory / "demand_history.csv", keep_default_na=False)
    calendar = pd.read_csv(directory / "known_calendar.csv", keep_default_na=False)
    history["date"] = pd.to_datetime(history["date"])
    calendar["date"] = pd.to_datetime(calendar["date"])
    return Bundle(root, report, read_json("source_config.json"), nodes, inventory, history,
                  calendar, read_json("network_config.json"), read_json("cost_config.json"),
                  read_json("scenario_config.json"))


def scenario_events(bundle: Bundle, scenario_id: str) -> pd.DataFrame:
    scenario = bundle.scenario(scenario_id)
    records = []
    start = pd.Timestamp(bundle.scenarios["simulation_start_date"])
    for key, kind in (("demand_events", "需求扰动"), ("transport_delay_events", "运输延迟"),
                      ("supply_limit_events", "供应限制")):
        for event in scenario[key]:
            if key == "demand_events":
                targets = event["terminal_ids"]
                target = "全部终端" if targets == "all" else "、".join(targets)
                impact = f"需求 × {event['multiplier']:g}"
            elif key == "transport_delay_events":
                target = f"{event['from_node']} → {event['to_node']}"
                impact = f"发运提前期增加 {event['extra_lead_time_days']} 天"
            else:
                target = event["supplier_id"]
                impact = f"生产能力 × {event['capacity_multiplier']:g}"
            first = start + pd.Timedelta(days=event["start_day"] - 1)
            last = start + pd.Timedelta(days=event["end_day"] - 1)
            records.append({"类型": kind, "对象": target,
                            "实验日": f"第 {event['start_day']}–{event['end_day']} 天",
                            "日期": f"{first:%Y-%m-%d} 至 {last:%Y-%m-%d}", "条件": impact})
    return pd.DataFrame(records, columns=["类型", "对象", "实验日", "日期", "条件"])
