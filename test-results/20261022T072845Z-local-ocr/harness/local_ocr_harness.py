from __future__ import annotations

import hashlib
import json
import logging
import os
import platform
import re
import sys
import time
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ship_invoice_agent.config import Settings
from ship_invoice_agent.ocr import OCRClient
from ship_invoice_agent.parsers import DocumentRouter


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_") or "document"


class RecordingOCRClient(OCRClient):
    def __init__(self, settings: Settings, logger: logging.Logger):
        super().__init__(settings)
        self.logger = logger
        self.staged: list[dict[str, Any]] = []
        self.requests: list[dict[str, Any]] = []
        self.url_sources: dict[str, str] = {}

    def stage_file(self, source: Path) -> tuple[Path, str]:
        target, url = super().stage_file(source)
        record = {
            "source": str(source),
            "target": str(target),
            "file_url": url,
            "size_bytes": source.stat().st_size,
            "sha256": sha256(source),
        }
        self.staged.append(record)
        self.url_sources[url] = str(source)
        self.logger.info("stage_file %s", json.dumps(record, ensure_ascii=False))
        return target, url

    def recognize(self, file_url: str) -> dict[str, Any] | list[Any]:
        started = time.perf_counter()
        record: dict[str, Any] = {
            "started_at": utc_now(),
            "endpoint": self.settings.ocr_api_url,
            "source": self.url_sources.get(file_url),
            "payload": {
                "file": file_url,
                "task": self.settings.ocr_task,
                "callBack": self.settings.ocr_callback,
                "taskType": self.settings.ocr_task_type,
            },
            "timeout_seconds": [
                self.settings.ocr_connect_timeout_seconds,
                self.settings.ocr_read_timeout_seconds,
            ],
        }
        self.logger.info("ocr_request %s", json.dumps(record, ensure_ascii=False))
        try:
            result = super().recognize(file_url)
            record.update(
                {
                    "status": "success",
                    "response_top_level_type": type(result).__name__,
                    "response_keys": sorted(result) if isinstance(result, dict) else None,
                }
            )
            return result
        except Exception as exc:
            record.update(
                {
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
            self.logger.exception("ocr_request_failed file_url=%s", file_url)
            raise
        finally:
            record["elapsed_seconds"] = round(time.perf_counter() - started, 6)
            record["finished_at"] = utc_now()
            self.requests.append(record)
            self.logger.info("ocr_result %s", json.dumps(record, ensure_ascii=False))


def main() -> int:
    repo = Path(os.environ["SHIP_REPO"]).resolve()
    result_dir = Path(os.environ["SHIP_RESULT_DIR"]).resolve()
    ocr_port = int(os.environ["SHIP_OCR_PORT"])
    file_service_name = os.environ["SHIP_FILE_SERVICE_NAME"]
    result_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = result_dir / "raw_ocr"
    text_dir = result_dir / "ocr_text"
    document_dir = result_dir / "documents"
    for directory in (raw_dir, text_dir, document_dir):
        directory.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger("local_ocr_harness")
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)sZ %(levelname)s %(message)s")
    file_handler = logging.FileHandler(result_dir / "client_router.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.handlers[:] = [file_handler, stream_handler]

    inputs = [
        repo / "tests/fixtures/documents/PDF示例账单1.pdf",
        repo / "tests/fixtures/documents/PDF 示例账单2.pdf",
        repo / "tests/fixtures/documents/sample-pre-alert.eml",
        repo / "tests/fixtures/documents/图片示例.png",
    ]
    for path in inputs:
        if not path.is_file():
            raise FileNotFoundError(path)

    input_records = [
        {
            "path": str(path),
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "sha256": sha256(path),
        }
        for path in inputs
    ]

    settings = Settings(
        llm_api_key=None,
        ocr_api_url=f"http://127.0.0.1:{ocr_port}/process",
        local_file_server_root=repo / ".runtime/files",
        local_file_base_url=f"http://{file_service_name}:8081",
        ocr_task="orderChange",
        ocr_callback="",
        ocr_task_type="",
        ocr_connect_timeout_seconds=10,
        ocr_read_timeout_seconds=300,
        ocr_max_retries=2,
    )
    client = RecordingOCRClient(settings, logger)
    router = DocumentRouter(settings, client)

    started_at = utc_now()
    started_perf = time.perf_counter()
    top_level_results: list[dict[str, Any]] = []
    documents: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    raw_index = 0

    for top_path in inputs:
        top_started = time.perf_counter()
        logger.info("top_level_start path=%s", top_path)
        try:
            parsed = router.parse(top_path)
            top_record = {
                "path": str(top_path),
                "name": top_path.name,
                "status": "success",
                "expanded_document_count": len(parsed),
                "elapsed_seconds": round(time.perf_counter() - top_started, 6),
            }
            top_level_results.append(top_record)
            for raw in parsed:
                record = {
                    "top_level_source": top_path.name,
                    "source_path": str(raw.source_path),
                    "source_name": raw.source_name,
                    "source_type": raw.source_type,
                    "parent_source": raw.parent_source,
                    "file_hash": raw.file_hash,
                    "content_chars": len(raw.content),
                    "has_ocr_raw": "ocr_raw" in raw.metadata,
                    "file_url": raw.metadata.get("file_url"),
                }
                documents.append(record)
                index = len(documents)
                (document_dir / f"{index:02d}_{safe_name(raw.source_name)}.txt").write_text(
                    raw.content, encoding="utf-8"
                )
                if "ocr_raw" in raw.metadata:
                    raw_index += 1
                    raw_ocr = raw.metadata["ocr_raw"]
                    prefix = (
                        f"{raw_index:02d}_{safe_name(top_path.name)}__"
                        f"{safe_name(raw.source_name)}"
                    )
                    (raw_dir / f"{prefix}.json").write_text(
                        json.dumps(raw_ocr, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    full_text = ""
                    if isinstance(raw_ocr, dict):
                        value = raw_ocr.get("full_text")
                        if isinstance(value, str):
                            full_text = value
                    (text_dir / f"{prefix}.txt").write_text(full_text, encoding="utf-8")
            logger.info("top_level_success %s", json.dumps(top_record, ensure_ascii=False))
        except Exception as exc:
            error = {
                "path": str(top_path),
                "name": top_path.name,
                "status": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(),
                "elapsed_seconds": round(time.perf_counter() - top_started, 6),
            }
            errors.append(error)
            top_level_results.append(error)
            logger.exception("top_level_failed path=%s", top_path)

    email_name = "sample-pre-alert.eml"
    email_docs = [item for item in documents if item["top_level_source"] == email_name]
    email_attachments = [item for item in email_docs if item["parent_source"] == email_name]
    ocr_documents = [item for item in documents if item["has_ocr_raw"]]
    file_hashes: dict[str, list[str]] = {}
    for item in documents:
        file_hashes.setdefault(item["file_hash"], []).append(
            f"{item['top_level_source']}::{item['source_name']}"
        )
    duplicates = {key: value for key, value in file_hashes.items() if len(value) > 1}

    assertions: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str) -> None:
        assertions.append({"name": name, "passed": bool(passed), "detail": detail})

    check("four_top_level_inputs", len(inputs) == 4, f"observed={len(inputs)}")
    check(
        "all_top_level_completed",
        not errors and all(item["status"] == "success" for item in top_level_results),
        f"errors={len(errors)}",
    )
    check("email_body_plus_three_attachments", len(email_docs) == 4, f"observed={len(email_docs)}")
    check(
        "email_attachment_parent_links",
        len(email_attachments) == 3,
        f"observed={len(email_attachments)}",
    )
    check("six_ocr_documents", len(ocr_documents) == 6, f"observed={len(ocr_documents)}")
    check("six_client_requests", len(client.requests) == 6, f"observed={len(client.requests)}")
    check(
        "all_ocr_requests_succeeded",
        len(client.requests) == 6 and all(item["status"] == "success" for item in client.requests),
        ", ".join(item["status"] for item in client.requests),
    )

    local_schema_ok = True
    nonempty_text_ok = True
    for raw_path in sorted(raw_dir.glob("*.json")):
        data = json.loads(raw_path.read_text(encoding="utf-8"))
        local_schema_ok &= bool(
            isinstance(data, dict)
            and data.get("success") is True
            and data.get("task") == "orderChange"
            and isinstance(data.get("pages"), list)
            and data.get("pages")
            and isinstance(data.get("full_text"), str)
        )
        nonempty_text_ok &= bool(isinstance(data, dict) and data.get("full_text", "").strip())
    check("local_response_schema", local_schema_ok and raw_index == 6, f"responses={raw_index}")
    check("all_ocr_text_nonempty", nonempty_text_ok and raw_index == 6, f"responses={raw_index}")

    image_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in text_dir.glob("*.txt")
        if ".png" in path.name
    )
    check(
        "image_stable_anchors",
        "INVOICE" in image_text.upper() and "USD" in image_text.upper(),
        "required=INVOICE,USD",
    )
    email_body = ""
    for index, item in enumerate(documents, start=1):
        if item["source_name"] == email_name and item["parent_source"] is None:
            email_body = (document_dir / f"{index:02d}_{safe_name(email_name)}.txt").read_text(
                encoding="utf-8", errors="replace"
            )
            break
    required_email = ["OOLU4055331281", "H/C $50", "FREIGHT PREPAID"]
    check(
        "email_body_stable_anchors",
        all(value in email_body for value in required_email),
        f"required={required_email}",
    )

    finished_at = utc_now()
    summary = {
        "run": {
            "started_at": started_at,
            "finished_at": finished_at,
            "elapsed_seconds": round(time.perf_counter() - started_perf, 6),
            "python": sys.version,
            "platform": platform.platform(),
            "repo": str(repo),
        },
        "configuration": {
            "ocr_api_url": settings.ocr_api_url,
            "local_file_base_url": settings.local_file_base_url,
            "local_file_server_root": str(settings.local_file_server_root),
            "ocr_task": settings.ocr_task,
            "ocr_callback": settings.ocr_callback,
            "ocr_task_type": settings.ocr_task_type,
            "timeouts_seconds": [
                settings.ocr_connect_timeout_seconds,
                settings.ocr_read_timeout_seconds,
            ],
            "ocr_max_retries": settings.ocr_max_retries,
            "llm_called": False,
            "external_ocr_called": False,
            "apply_used": False,
        },
        "counts": {
            "top_level_inputs": len(inputs),
            "expanded_documents": len(documents),
            "email_documents_including_body": len(email_docs),
            "email_attachments": len(email_attachments),
            "ocr_documents": len(ocr_documents),
            "ocr_requests": len(client.requests),
            "duplicate_file_hash_groups": len(duplicates),
        },
        "inputs": input_records,
        "top_level_results": top_level_results,
        "documents": documents,
        "staged_files": client.staged,
        "ocr_requests": client.requests,
        "duplicate_file_hashes": duplicates,
        "assertions": assertions,
        "errors": errors,
    }
    (result_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (result_dir / "requests.json").write_text(
        json.dumps(client.requests, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (result_dir / "inputs.json").write_text(
        json.dumps(input_records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (result_dir / "documents.json").write_text(
        json.dumps(documents, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (result_dir / "errors.json").write_text(
        json.dumps(errors, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    failed = [item for item in assertions if not item["passed"]]
    logger.info(
        "run_complete documents=%d ocr_requests=%d assertions=%d failed_assertions=%d errors=%d",
        len(documents),
        len(client.requests),
        len(assertions),
        len(failed),
        len(errors),
    )
    return 1 if errors or failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
