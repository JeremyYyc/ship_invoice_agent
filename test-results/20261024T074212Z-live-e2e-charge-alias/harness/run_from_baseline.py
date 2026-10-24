"""Run the live harness and verify charge-alias precedence."""

import importlib.util
import json
import os
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.models import ExtractedDocument
from ship_invoice_agent.normalization import NormalizationService

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

exit_code = 0 if os.getenv("REUSE_EXISTING_RESULTS") == "1" else module.main()
settings = Settings()

configuration_path = RESULT_DIR / "configuration.json"
configuration = json.loads(configuration_path.read_text(encoding="utf-8"))
configuration.update(
    {
        "llm_enable_thinking": settings.llm_enable_thinking,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_total_time_budget_seconds": settings.llm_total_time_budget_seconds,
        "llm_max_retries": settings.llm_max_retries,
        "provider_sdk_retries": 0,
        "ocr_adapter": "LocalOCRAdapter",
        "charge_alias_conflict_policy": settings.charge_alias_conflict_policy,
    }
)
module.write_json(configuration_path, configuration)

email_operations = json.loads(
    (RESULT_DIR / "outputs" / "email.json").read_text(encoding="utf-8")
)
email_charge = email_operations[0]
warnings = email_charge["audit_trail"]["warnings"]
live_extraction = json.loads(
    (
        RESULT_DIR
        / "model_extractions"
        / "email_01_sample-pre-alert.eml.json"
    ).read_text(encoding="utf-8")
)["extracted"]["charges"][0]
live_model_name = live_extraction["charge_item"]
live_model_conflicted = live_model_name != "Handling Charge"

historical_path = (
    REPO
    / "test-results"
    / "20261024T072521Z-live-e2e-flash-adapter"
    / "model_extractions"
    / "email_01_sample-pre-alert.eml.json"
)
historical_document = ExtractedDocument.model_validate(
    json.loads(historical_path.read_text(encoding="utf-8"))["extracted"]
)
historical_result = NormalizationService(
    settings.charge_aliases_path,
    settings.party_aliases_path,
    settings.charge_alias_conflict_policy,
).normalize_document_with_audit(historical_document)
historical_resolution = historical_result.charge_resolutions[0]

alias_regression = {
    "expected_charge_item": "Handling Charge",
    "actual_charge_item": email_charge["charge_details"]["charge_item"],
    "actual_operation": email_charge["operation"],
    "live_model_charge_item": live_model_name,
    "live_model_conflicted": live_model_conflicted,
    "model_conflict_recorded": "MODEL_CHARGE_NAME_CONFLICT" in warnings,
    "model_override_recorded": "MODEL_CHARGE_NAME_OVERRIDDEN" in warnings,
    "historical_conflict_case": {
        "model_charge_item": historical_document.charges[0].charge_item,
        "final_charge_item": historical_result.document.charges[0].charge_item,
        "conflict": historical_resolution.conflict,
        "requires_review": historical_resolution.requires_review,
        "warnings": historical_resolution.warnings,
    },
}
live_audit_is_correct = (
    alias_regression["model_conflict_recorded"]
    and alias_regression["model_override_recorded"]
    if live_model_conflicted
    else not alias_regression["model_conflict_recorded"]
    and not alias_regression["model_override_recorded"]
)
alias_regression["passed"] = all(
    [
        alias_regression["actual_charge_item"]
        == alias_regression["expected_charge_item"],
        live_audit_is_correct,
        historical_result.document.charges[0].charge_item == "Handling Charge",
        historical_resolution.conflict,
        historical_resolution.requires_review,
        "MODEL_CHARGE_NAME_CONFLICT" in historical_resolution.warnings,
        "MODEL_CHARGE_NAME_OVERRIDDEN" in historical_resolution.warnings,
    ]
)
module.write_json(RESULT_DIR / "alias_regression.json", alias_regression)

raise SystemExit(1 if exit_code or not alias_regression["passed"] else 0)
