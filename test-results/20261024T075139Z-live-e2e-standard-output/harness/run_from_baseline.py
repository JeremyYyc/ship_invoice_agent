"""Run the live harness and enforce the assignment's public output schema."""

import importlib.util
import json
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.output import AssignmentBillOperation

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

exit_code = module.main()
settings = Settings()

configuration_path = RESULT_DIR / "configuration.json"
configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
configuration.update(
    {
        "output_schema_mode": settings.output_schema_mode,
        "llm_enable_thinking": settings.llm_enable_thinking,
        "ocr_adapter": "LocalOCRAdapter",
        "charge_alias_conflict_policy": settings.charge_alias_conflict_policy,
    }
)
module.write_json(configuration_path, configuration)

expected_top_level = {
    "operation",
    "document_info",
    "transaction_party",
    "charge_details",
    "financial_attributes",
    "audit_trail",
}
records = 0
errors: list[str] = []
for path in sorted((RESULT_DIR / "outputs").glob("*.json")):
    for index, item in enumerate(json.loads(path.read_text(encoding="utf-8"))):
        records += 1
        try:
            AssignmentBillOperation.model_validate(item)
        except Exception as exc:
            errors.append(f"{path.name}[{index}] schema error: {type(exc).__name__}")
        if set(item) != expected_top_level:
            errors.append(f"{path.name}[{index}] top-level keys differ")
        if set(item.get("audit_trail", {})) != {"ai_reasoning"}:
            errors.append(f"{path.name}[{index}] audit_trail has non-standard fields")

regression = {
    "schema": "assignment",
    "records_checked": records,
    "expected_top_level_fields": sorted(expected_top_level),
    "audit_trail_fields": ["ai_reasoning"],
    "errors": errors,
    "passed": records > 0 and not errors,
}
module.write_json(RESULT_DIR / "output_schema_regression.json", regression)

raise SystemExit(1 if exit_code or not regression["passed"] else 0)
