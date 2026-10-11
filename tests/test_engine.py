"""Pure-service regression suite; no Streamlit socket or external API needed."""
from pathlib import Path
import json
import shutil
import tempfile
import unittest
from unittest.mock import patch

from demo.provider import load_bundle
from demo.engine import (context, verify_engine, forecast_rows, holdout_score,
                         initial_plan, paired_experiment, public_result, daily_rows)
from demo.agent_service import evidence_answer, execute_tool, live_answer

ROOT = Path(__file__).resolve().parents[1]


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(ROOT)
        cls.ctx = context(cls.bundle)

    def test_manifest_and_forecast_boundary(self):
        self.assertEqual(len(forecast_rows(self.ctx, "C04")), 30)
        self.assertEqual(self.ctx.snapshot_id, self.bundle.snapshot_id)
        score = holdout_score(self.ctx, "C04")
        self.assertEqual(score["holdout_days"], 28)
        self.assertGreater(score["naive_wape"], score["model_wape"])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(ROOT / "engine_input", root / "engine_input")
            shutil.copytree(ROOT / "vendor" / "supply_chain", root / "vendor" / "supply_chain")
            shutil.copytree(ROOT / "data", root / "data")
            shutil.copy2(ROOT / "engine_manifest.json", root / "engine_manifest.json")
            with (root / "engine_input" / "future_demand.csv").open("ab") as stream:
                stream.write(b"\n")
            with self.assertRaisesRegex(ValueError, "已变动"):
                verify_engine(root, self.bundle.snapshot_id)

    def test_first_day_order_constraints(self):
        for pid in ("S1", "S2"):
            plan = initial_plan(self.ctx, pid, "C04")
            self.assertEqual(plan["upstream"], "R02")
            self.assertEqual(plan["requested_units"] % plan["order_multiple"], 0)
            self.assertLessEqual(plan["requested_units"], plan["capacity"]-plan["inventory_position"])
        self.assertEqual(initial_plan(self.ctx, "S2", "S01")["requested_units"], 0)

    def test_all_scenarios_are_recomputed_and_audited(self):
        for scenario in self.ctx.dataset.scenarios:
            with self.subTest(scenario=scenario):
                experiment = paired_experiment(self.ctx, scenario)
                self.assertEqual(len(experiment["S1"]["run"].days), 30)
                self.assertEqual(len(daily_rows(experiment, "C04")), 60)
                exported = public_result(experiment)
                self.assertNotIn("future_demand", exported)
                self.assertEqual(exported["warmup_days"], 0)
                self.assertEqual(exported["S1"]["scenario_id"], scenario)
        stressed = paired_experiment(self.ctx, "supply_limit")
        self.assertGreater(stressed["S2"]["metrics"]["total_cost"],
                           stressed["S1"]["metrics"]["total_cost"])

    def test_agent_tools_and_offline_evidence(self):
        response = evidence_answer(self.ctx, "解释 C04 的补货和基准场景", "C04", "baseline")
        self.assertEqual(len(response["trace"]), 4)
        self.assertIn(response["run_id"], response["answer"])
        self.assertEqual(response["trace"][-1]["result"]["run_id"], response["run_id"])
        with self.assertRaises(ValueError):
            execute_tool(self.ctx, "unknown_tool", {})
        with self.assertRaises(ValueError):
            execute_tool(self.ctx, "forecast_demand", {"node_id": "BAD"})

    def test_live_agent_requires_tool_trace_and_restricts_scope(self):
        with patch("demo.agent_service._deepseek_chat", return_value={"content": "未经验证 99%"}):
            with self.assertRaisesRegex(RuntimeError, "未调用业务工具"):
                live_answer(self.ctx, "如何补货", "C04", "baseline", "test-key")
        calls = [
            {"content": None, "tool_calls": [{"id": "call1", "function": {
                "name": "read_inventory", "arguments": json.dumps({"node_id": "C04"})}}]},
            {"content": "C04 当前初始库存为 273 件。"},
        ]
        with patch("demo.agent_service._deepseek_chat", side_effect=calls):
            answer = live_answer(self.ctx, "初始库存", "C04", "baseline", "test-key")
        self.assertEqual(len(answer["trace"]), 1)
        self.assertEqual(answer["trace"][0]["result"]["initial_on_hand_units"], 273)


if __name__ == "__main__":
    unittest.main()
