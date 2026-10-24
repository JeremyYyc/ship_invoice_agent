"""Run the committed live harness and add Adapter/configuration metrics."""

import importlib.util
import json
from pathlib import Path

from ship_invoice_agent.config import Settings

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
        "llm_enable_thinking": settings.llm_enable_thinking,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_total_time_budget_seconds": settings.llm_total_time_budget_seconds,
        "llm_max_retries": settings.llm_max_retries,
        "provider_sdk_retries": 0,
        "ocr_adapter": "LocalOCRAdapter",
    }
)
module.write_json(configuration_path, configuration)

raw_ocr_chars = 0
for path in (RESULT_DIR / "raw_ocr").glob("*.json"):
    raw_ocr = json.loads(path.read_text(encoding="utf-8"))
    raw_ocr_chars += len(json.dumps(raw_ocr, ensure_ascii=False, separators=(",", ":")))

canonical_ocr_chars = 0
documents: list[dict] = []
for path in (RESULT_DIR / "raw_documents").glob("*.json"):
    for document in json.loads(path.read_text(encoding="utf-8")):
        content_chars = len(document.get("content", ""))
        documents.append(
            {
                "source_name": document.get("source_name"),
                "source_type": document.get("source_type"),
                "content_chars": content_chars,
            }
        )
        if document.get("source_type") != "eml":
            canonical_ocr_chars += content_chars

reduction = 0.0 if not raw_ocr_chars else 1 - canonical_ocr_chars / raw_ocr_chars
module.write_json(
    RESULT_DIR / "adapter_metrics.json",
    {
        "adapter": "LocalOCRAdapter",
        "raw_ocr_json_chars": raw_ocr_chars,
        "canonical_ocr_chars": canonical_ocr_chars,
        "character_reduction": round(reduction, 6),
        "documents": documents,
    },
)

raise SystemExit(exit_code)
