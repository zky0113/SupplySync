"""UI integration smoke test where Streamlit AppTest is supported.

The Windows sandbox blocks AppTest's asyncio socketpair initialization, so
the deterministic engine/Agent service tests are the runnable gate here.
"""
from pathlib import Path
import sys
import unittest

from demo.views import network_view, history_view, simulation_view, agent_view

ROOT = Path(__file__).resolve().parents[1]


class WorkbenchTests(unittest.TestCase):
    def test_provided_partner_logo_is_packaged_and_referenced(self):
        asset = ROOT / "assets" / "shulian-tech-logo.jpg"
        self.assertTrue(asset.is_file())
        self.assertTrue(asset.read_bytes().startswith(b"\xff\xd8\xff"))
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("shulian-tech-logo.jpg", source)
        self.assertIn('alt="数联科技标志"', source)

    def test_current_modules_are_wired(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        self.assertIn("simulation_view(bundle, scenario_id, selected)", source)
        self.assertIn("agent_view(bundle, selected, scenario_id)", source)
        self.assertNotIn("simulation_disabled", source)
        for view in (network_view, history_view, simulation_view, agent_view):
            self.assertTrue(callable(view))

    @unittest.skipIf(sys.platform == "win32", "AppTest asyncio socketpair is blocked in this Windows sandbox")
    def test_streamlit_app_renders(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(app.exception, str(app.exception))
        self.assertFalse(app.error, str(app.error))
        self.assertEqual([tab.label for tab in app.tabs],
                         ["网络与库存", "需求与预测", "仿真对比", "Agent 与证据"])


if __name__ == "__main__":
    unittest.main()
