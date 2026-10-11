from pathlib import Path
import json
import shutil
import tempfile
import unittest

import pandas as pd

from demo.provider import load_bundle, validate_snapshot, sha256, scenario_events

ROOT = Path(__file__).resolve().parents[1]


class ValidatedInputTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = load_bundle(ROOT)

    def test_counts_dates_and_initial_stock(self):
        self.assertEqual(len(self.bundle.nodes), 15)
        self.assertEqual(len(self.bundle.edges), 14)
        self.assertEqual(len(self.bundle.history), 1680)
        self.assertEqual(self.bundle.history["date"].nunique(), 210)
        self.assertEqual(int(self.bundle.inventory["on_hand_units"].sum()), 4293)
        self.assertEqual(str(self.bundle.history["date"].min().date()), "2026-02-01")
        self.assertEqual(str(self.bundle.history["date"].max().date()), "2026-08-29")

    def test_branch_aggregation_matches_terminal_rows(self):
        for node_id in self.bundle.nodes["node_id"]:
            daily = self.bundle.daily_history(node_id)
            terminals = self.bundle.descendants(node_id)
            expected = self.bundle.history[self.bundle.history["terminal_id"].isin(terminals)]
            expected = expected.groupby("date")["demand_units"].sum()
            pd.testing.assert_series_equal(daily.set_index("date")["demand_units"], expected)
        self.assertEqual(self.bundle.descendants("C04"), ["T07", "T08"])
        self.assertEqual(int(self.bundle.daily_history("S01")["demand_units"].sum()), 55532)

    def test_date_filter_and_rolling_are_retrospective(self):
        complete = self.bundle.daily_history("S01")
        daily = self.bundle.daily_history("S01", "2026-08-01", "2026-08-07")
        self.assertEqual(len(daily), 7)
        self.assertEqual(daily["moving_average_7d"].isna().sum(), 0)
        expected = complete.set_index("date").loc[pd.Timestamp("2026-08-01"), "moving_average_7d"]
        self.assertAlmostEqual(daily.iloc[0]["moving_average_7d"], expected)
        self.assertAlmostEqual(daily.iloc[-1]["moving_average_7d"], daily["demand_units"].mean())

    def test_scenario_dates_and_targets_come_from_config(self):
        frame = scenario_events(self.bundle, "transport_delay")
        self.assertEqual(frame.iloc[0]["对象"], "R02 → C04")
        self.assertEqual(frame.iloc[0]["日期"], "2026-09-05 至 2026-09-14")
        self.assertEqual(frame.iloc[0]["条件"], "发运提前期增加 2 天")
        self.assertTrue(scenario_events(self.bundle, "baseline").empty)
        self.assertEqual(len(scenario_events(self.bundle, "combined_stress")), 3)

    def test_bundle_excludes_future_truth_and_policy_results(self):
        self.assertFalse((ROOT / "data" / "future_demand.csv").exists())
        self.assertFalse(hasattr(self.bundle, "future_demand"))
        self.assertFalse(hasattr(self.bundle, "metrics"))
        self.assertNotIn("future_total_demand_units_by_scenario", self.bundle.report["summary"])

    def _copy_snapshot(self, path):
        for directory in ("data", "reports", "vendor"):
            shutil.copytree(ROOT / directory, path / directory)

    def test_modified_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            self._copy_snapshot(path)
            with (path / "data" / "demand_history.csv").open("ab") as stream:
                stream.write(b"\n")
            with self.assertRaisesRegex(ValueError, "发生变化"):
                validate_snapshot(path)

    def test_editing_report_cannot_hide_capacity_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            self._copy_snapshot(path)
            file = path / "data" / "nodes.csv"
            file.write_bytes(file.read_bytes().replace(b"1238,3263", b"1238,1"))
            report_path = path / "reports" / "validation_report.json"
            report = json.loads(report_path.read_text(encoding="utf-8"))
            report["file_sha256"]["data/nodes.csv"] = sha256(file.read_bytes())
            report["snapshot_id"] = sha256(json.dumps(report["file_sha256"], sort_keys=True).encode())[:16]
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "生成结果不一致"):
                validate_snapshot(path)


if __name__ == "__main__":
    unittest.main()
