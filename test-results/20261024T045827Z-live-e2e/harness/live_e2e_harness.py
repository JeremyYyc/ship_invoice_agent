import json
import logging
import platform
import subprocess
import sys
import time
import traceback
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from ship_invoice_agent.config import Settings
from ship_invoice_agent.evaluation import evaluate_output
from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.llm import create_chat_model
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import ExtractedDocument, RawDocument
from ship_invoice_agent.ocr import OCRClient
from ship_invoice_agent.output import serialize_operations
from ship_invoice_agent.parsers import DocumentRouter
from ship_invoice_agent.workflow import InvoiceAgent

RESULT_DIR = Path(__file__).resolve().parents[1]
REPO = Path(__file__).resolve().parents[3]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )


def safe_name(value: str) -> str:
    chars = [char if char.isalnum() or char in {"-", "_", "."} else "_" for char in value]
    return "".join(chars).strip("_") or "document"


class RecordingOCRClient(OCRClient):
    def __init__(self, settings: Settings, case_id: str):
        super().__init__(settings)
        self.case_id = case_id
        self.calls: list[dict[str, Any]] = []

    def recognize(self, file_url: str) -> dict[str, Any] | list[Any]:
        started = time.perf_counter()
        started_at = utc_now()
        try:
            response = super().recognize(file_url)
        except Exception as exc:
            self.calls.append(
                {
                    "file_url": file_url,
                    "started_at": started_at,
                    "elapsed_seconds": round(time.perf_counter() - started, 6),
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
            )
            raise
        index = len(self.calls) + 1
        source_name = Path(unquote(urlparse(file_url).path)).name
        output_name = f"{self.case_id}_{index:02d}_{safe_name(source_name)}.json"
        write_json(RESULT_DIR / "raw_ocr" / output_name, response)
        self.calls.append(
            {
                "file_url": file_url,
                "started_at": started_at,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "status": "success",
                "artifact": f"raw_ocr/{output_name}",
            }
        )
        return response


class RecordingRouter(DocumentRouter):
    def __init__(self, settings: Settings, ocr_client: OCRClient, case_id: str):
        super().__init__(settings, ocr_client)
        self.case_id = case_id

    def parse(
        self,
        path: Path,
        *,
        file_url: str | None = None,
        parent_source: str | None = None,
        depth: int = 0,
    ) -> list[RawDocument]:
        documents = super().parse(
            path,
            file_url=file_url,
            parent_source=parent_source,
            depth=depth,
        )
        if depth == 0:
            records = []
            for document in documents:
                metadata = {
                    key: value for key, value in document.metadata.items() if key != "ocr_raw"
                }
                records.append(
                    {
                        **document.model_dump(mode="json", exclude={"metadata"}),
                        "metadata": metadata,
                    }
                )
            write_json(RESULT_DIR / "raw_documents" / f"{self.case_id}.json", records)
        return documents


class RecordingExtractor:
    def __init__(self, base: InvoiceExtractor, case_id: str):
        self.base = base
        self.case_id = case_id
        self.index = 0

    def extract(self, document: RawDocument) -> ExtractedDocument:
        started = time.perf_counter()
        extracted = self.base.extract(document)
        self.index += 1
        write_json(
            RESULT_DIR
            / "model_extractions"
            / f"{self.case_id}_{self.index:02d}_{safe_name(document.source_name)}.json",
            {
                "source_name": document.source_name,
                "parent_source": document.parent_source,
                "elapsed_seconds": round(time.perf_counter() - started, 6),
                "extracted": extracted.model_dump(mode="json"),
            },
        )
        return extracted


def command_output(command: list[str]) -> dict[str, Any]:
    result = subprocess.run(command, cwd=REPO, text=True, capture_output=True, check=False)
    return {
        "command": command,
        "returncode": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }


def main() -> int:
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(RESULT_DIR / "runtime.log", encoding="utf-8"),
            logging.StreamHandler(sys.stderr),
        ],
    )
    logger = logging.getLogger("live_e2e")
    started_at = utc_now()
    started = time.perf_counter()
    settings = Settings()
    key_configured = bool(settings.llm_api_key and settings.llm_api_key.get_secret_value())
    safe_configuration = {
        "llm_protocol": settings.llm_protocol,
        "llm_provider": settings.llm_provider,
        "llm_model_name": settings.llm_model_name,
        "llm_base_host": urlparse(settings.llm_base_url or "").hostname,
        "llm_api_key_configured": key_configured,
        "llm_structured_output_mode": settings.llm_structured_output_mode,
        "ocr_api_url": settings.ocr_api_url,
        "local_file_base_url": settings.local_file_base_url,
        "apply_bill_changes": False,
    }
    write_json(RESULT_DIR / "configuration.json", safe_configuration)

    environment = {
        "started_at": started_at,
        "python": sys.version,
        "platform": platform.platform(),
        "packages": {
            name: version(name)
            for name in ("langchain", "langchain-openai", "pydantic", "requests")
        },
        "git": command_output(["git", "status", "--short"]),
        "docker_compose": command_output(["docker", "compose", "--env-file", ".env", "ps"]),
    }
    write_json(RESULT_DIR / "environment.json", environment)

    logger.info(
        "creating configured model provider=%s model=%s",
        settings.llm_provider,
        settings.llm_model_name,
    )
    llm = create_chat_model(settings)
    check_started = time.perf_counter()
    check_response = llm.invoke('Return exactly this JSON object: {"status":"ok","amount":1.25}')
    check_content = (
        str(check_response.content).strip().removeprefix("```json").removesuffix("```").strip()
    )
    parsed_check = json.loads(check_content)
    if parsed_check != {"status": "ok", "amount": 1.25}:
        raise ValueError(f"model JSON conformance failed: {parsed_check}")
    write_json(
        RESULT_DIR / "model_check.json",
        {
            "status": "success",
            "provider": settings.llm_provider,
            "model": settings.llm_model_name,
            "elapsed_seconds": round(time.perf_counter() - check_started, 6),
            "response": parsed_check,
        },
    )

    cases = [
        {
            "id": "pdf1",
            "input": REPO / "tests/fixtures/documents/PDF示例账单1.pdf",
            "accounting_entity": "Neptune Shipping Limited",
        },
        {
            "id": "pdf2",
            "input": REPO / "tests/fixtures/documents/PDF 示例账单2.pdf",
            "accounting_entity": "Pilot Aiship Tech co., Ltd.",
        },
        {
            "id": "email",
            "input": REPO / "tests/fixtures/documents/sample-pre-alert.eml",
            "accounting_entity": "Pilot Aiship Tech co., Ltd.",
        },
        {
            "id": "image",
            "input": REPO / "tests/fixtures/documents/图片示例.png",
            "accounting_entity": "USIAHALLAM",
        },
    ]
    case_results: list[dict[str, Any]] = []
    all_ocr_calls: list[dict[str, Any]] = []
    for case in cases:
        case_started = time.perf_counter()
        case_id = case["id"]
        logger.info("case_start id=%s input=%s", case_id, case["input"])
        ocr_client = RecordingOCRClient(settings, case_id)
        router = RecordingRouter(settings, ocr_client, case_id)
        extractor = RecordingExtractor(
            InvoiceExtractor(
                llm,
                REPO / "prompts/extract_document.md",
                structured_output_mode=settings.llm_structured_output_mode,
            ),
            case_id,
        )
        repository = JsonBillRepository(settings.existing_bills_path)
        agent = InvoiceAgent(settings, router, extractor, repository)
        try:
            operations = agent.process(
                case["input"],
                accounting_entity=case["accounting_entity"],
                apply=False,
            )
            output_path = RESULT_DIR / "outputs" / f"{case_id}.json"
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(
                serialize_operations(operations, settings.json_output_indent) + "\n",
                encoding="utf-8",
            )
            result = {
                "id": case_id,
                "input": str(case["input"]),
                "accounting_entity": case["accounting_entity"],
                "status": "success",
                "elapsed_seconds": round(time.perf_counter() - case_started, 6),
                "operation_count": len(operations),
                "operations": [operation.operation.value for operation in operations],
                "output": f"outputs/{case_id}.json",
                "ocr_request_count": len(ocr_client.calls),
                "model_extraction_count": extractor.index,
            }
            logger.info("case_success id=%s operations=%s", case_id, result["operations"])
        except Exception as exc:
            result = {
                "id": case_id,
                "input": str(case["input"]),
                "accounting_entity": case["accounting_entity"],
                "status": "error",
                "elapsed_seconds": round(time.perf_counter() - case_started, 6),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "ocr_request_count": len(ocr_client.calls),
                "model_extraction_count": extractor.index,
            }
            logger.exception("case_error id=%s", case_id)
        case_results.append(result)
        all_ocr_calls.extend([{"case_id": case_id, **call} for call in ocr_client.calls])
        write_json(RESULT_DIR / "cases.json", case_results)
        write_json(RESULT_DIR / "ocr_requests.json", all_ocr_calls)
        if result["status"] == "error":
            break

    evaluation = None
    pdf2_output = RESULT_DIR / "outputs/pdf2.json"
    if pdf2_output.exists():
        evaluation = evaluate_output(pdf2_output, REPO / "tests/golden/debit_note.json").as_dict()
        write_json(RESULT_DIR / "evaluation_pdf2.json", evaluation)

    summary = {
        "started_at": started_at,
        "finished_at": utc_now(),
        "elapsed_seconds": round(time.perf_counter() - started, 6),
        "story": (
            "local source -> local OCR HTTP -> RawDocument -> configured live LLM "
            "-> deterministic reconciliation -> BillOperation JSON"
        ),
        "model_check": "success",
        "configured_cases": len(cases),
        "completed_cases": len(case_results),
        "successful_cases": sum(item["status"] == "success" for item in case_results),
        "failed_cases": sum(item["status"] == "error" for item in case_results),
        "ocr_request_count": len(all_ocr_calls),
        "model_extraction_count": sum(item["model_extraction_count"] for item in case_results),
        "operation_count": sum(item.get("operation_count", 0) for item in case_results),
        "pdf2_golden_evaluation": evaluation,
        "apply_used": False,
        "existing_bills_modified": False,
    }
    write_json(RESULT_DIR / "summary.json", summary)
    docker_logs = command_output(
        [
            "docker",
            "compose",
            "--env-file",
            ".env",
            "logs",
            "--no-color",
            "--tail=500",
            "local-ocr",
            "file-server",
        ]
    )
    write_json(RESULT_DIR / "docker_logs.json", docker_logs)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if summary["failed_cases"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
