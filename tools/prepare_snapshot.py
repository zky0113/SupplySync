"""Create a byte-identical, independently validated display snapshot.

Only generator input files and generator/validator source are copied. No Agent,
credentials, policy-derived results or future demand CSV enter the display bundle.
Run explicitly when the source team's input changes; never refresh automatically.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
DISPLAY_FILES = (
    "nodes.csv", "initial_inventory.csv", "demand_history.csv", "known_calendar.csv",
    "network_config.json", "cost_config.json", "scenario_config.json",
)
GENERATOR_FILES = ("__init__.py", "schema.py", "io_utils.py", "generator.py", "validation.py")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def prepare(source: Path) -> dict:
    config_bytes = (source / "config" / "default.json").read_bytes()
    config = json.loads(config_bytes)
    sys.path.insert(0, str(source))
    from supply_data.generator import generate_payloads
    from supply_data.validation import validate_config, validate_dataset
    validate_config(config)
    payloads = generate_payloads(config)
    if payloads != generate_payloads(config):
        raise ValueError("Generator is not deterministic with the same configuration.")
    source_hashes = {}
    for name in (*DISPLAY_FILES, "future_demand.csv"):
        raw = (source / "data" / name).read_bytes()
        if raw != payloads[name]:
            raise ValueError(f"{name}: source bytes do not match configured seeded generator. "
                             "Review the source change before adopting a new snapshot.")
        source_hashes[name] = digest(raw)
    # Temporary generated inputs are used only for validator checks and discarded.
    with tempfile.TemporaryDirectory(prefix="supplysync-check-") as temporary:
        path = Path(temporary)
        for name, raw in payloads.items():
            (path / name).write_bytes(raw)
        summary = validate_dataset(path, config, verify_manifest=False)

    destination = ROOT / "data"
    destination.mkdir(parents=True, exist_ok=True)
    for name in DISPLAY_FILES:
        (destination / name).write_bytes((source / "data" / name).read_bytes())
    (destination / "source_config.json").write_bytes(config_bytes)
    vendor = ROOT / "vendor" / "supply_data"
    vendor.mkdir(parents=True, exist_ok=True)
    for name in GENERATOR_FILES:
        shutil.copyfile(source / "supply_data" / name, vendor / name)

    files = [f"data/{name}" for name in DISPLAY_FILES]
    files.append("data/source_config.json")
    files.extend(f"vendor/supply_data/{name}" for name in GENERATOR_FILES)
    hashes = {name: digest((ROOT / name).read_bytes()) for name in files}
    snapshot_id = digest(json.dumps(hashes, sort_keys=True).encode("utf-8"))[:16]
    # Simulator-only future demand summaries are excluded from the UI report.
    safe_summary = {key: value for key, value in summary.items()
                    if not key.startswith("future_demand") and key != "future_total_demand_units_by_scenario"}
    report = {
        "schema_version": "frontend-snapshot-1.0", "status": "passed",
        "snapshot_id": snapshot_id,
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_project": "teammate_agent_project", "data_kind": "synthetic_single_sku",
        "seed": config["seed"], "summary": safe_summary,
        "file_sha256": hashes, "source_input_sha256": source_hashes,
        "missing_source_metadata": [name for name in (
            "effective_config.json", "generation_manifest.json", "data_summary.json"
        ) if not (source / "data" / name).exists()],
        "checks": [
            "配置结构、四级邻接关系与完整连通性",
            "节点/库存字段、SKU、状态日期与容量约束",
            "运输提前期、运力、起订量与整批约束",
            "历史日期连续、终端日组合唯一且完整、需求非负整数",
            "促销日历、星期、历史促销标记一致",
            "成本非负并与配置一致",
            "场景目标、日期、倍率与外生扰动规则",
            "原始八个输入文件与指定 seed 生成器逐字节一致",
            "同配置重复生成一致，展示快照和校验源码 SHA-256 固定",
        ],
        "scope": "Checks establish synthetic input consistency and reproduction, not real-world "
                 "accuracy, forecast quality, simulator correctness or policy effectiveness.",
        "future_truth_in_display_bundle": False,
        "scenario_visibility": "Experiment designer preview only; not a future planner feed.",
    }
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    (reports / "validation_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": "passed", "snapshot_id": snapshot_id,
                      "summary": safe_summary}, ensure_ascii=False, indent=2))
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    arguments = parser.parse_args()
    prepare(arguments.source)
