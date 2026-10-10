"""Support both a regular Python install and Codex's bundled Python runtime."""
from __future__ import annotations

import os
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parent
packages = ROOT / ".runtime" / "packages"
if packages.is_dir():
    sys.path.insert(0, str(packages))
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
temporary = Path(os.environ.get("SUPPLYSYNC_TEMP_DIR", str(ROOT / ".runtime" / "temp")))
temporary.mkdir(parents=True, exist_ok=True)
os.environ["TEMP"] = str(temporary)
os.environ["TMP"] = str(temporary)
if __name__ == "__main__":
    arguments = sys.argv[1:] or ["streamlit"]
    mode = arguments[0]
    if mode == "streamlit":
        options = list(arguments[1:])
        if not any(option.startswith("--server.address") for option in options):
            options.append("--server.address=127.0.0.1")
        if not any(option.startswith("--server.port") for option in options):
            options.append("--server.port=8501")
        sys.argv = ["streamlit", "run", str(ROOT / "app.py"), *options]
        runpy.run_module("streamlit", run_name="__main__")
    elif mode == "test":
        sys.argv = ["unittest", "discover", "-s", "tests", "-v"]
        runpy.run_module("unittest", run_name="__main__")
    elif mode == "validate":
        from demo.provider import load_bundle
        bundle = load_bundle(ROOT)
        print(f"PASS: {len(bundle.nodes)} nodes, {len(bundle.edges)} edges, "
              f"{len(bundle.history)} history rows; snapshot {bundle.snapshot_id}")
    else:
        raise SystemExit("Usage: python bootstrap.py [streamlit|test|validate]")
