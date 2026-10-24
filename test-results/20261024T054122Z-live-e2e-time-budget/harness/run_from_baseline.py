"""Run the committed live harness while writing to this optimization result directory."""

import importlib.util
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HARNESS = (
    REPO
    / "test-results"
    / "20261024T045827Z-live-e2e"
    / "harness"
    / "live_e2e_harness.py"
)
RESULT_DIR = Path(__file__).resolve().parents[1]

spec = importlib.util.spec_from_file_location("baseline_live_e2e_harness", HARNESS)
if spec is None or spec.loader is None:
    raise RuntimeError(f"could not load harness: {HARNESS}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
module.RESULT_DIR = RESULT_DIR

raise SystemExit(module.main())
