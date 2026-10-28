"""Verify native Office reading with the live LLM or replay saved model outputs offline."""

import argparse
import hashlib
import json
import time
from collections import Counter
from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.cli import build_agent
from ship_invoice_agent.config import Settings
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import ExtractedDocument, Operation, RawDocument
from ship_invoice_agent.output import serialize_operations
from ship_invoice_agent.parsers import DocumentRouter
from ship_invoice_agent.workflow import InvoiceAgent

PROJECT = Path(__file__).resolve().parents[1]


class NoOCRClient:
    calls = 0

    def stage_file(self, path):
        self.calls += 1
        raise AssertionError("native Office reading must not stage files for OCR")

    def recognize(self, file_url):
        self.calls += 1
        raise AssertionError("native Office reading must not call OCR")


class RecordingExtractor:
    def __init__(self, delegate, output_dir):
        self.delegate = delegate
        self.output_dir = output_dir
        self.document = None

    def extract(self, raw):
        (self.output_dir / "raw_document.json").write_text(
            raw.model_dump_json(indent=2), encoding="utf-8"
        )
        self.document = self.delegate.extract(raw)
        (self.output_dir / "extracted_document.json").write_text(
            self.document.model_dump_json(indent=2), encoding="utf-8"
        )
        return self.document


class ReplayExtractor:
    def __init__(self, source_dir):
        self.raw = RawDocument.model_validate_json((source_dir / "raw_document.json").read_text())
        self.document = ExtractedDocument.model_validate_json(
            (source_dir / "extracted_document.json").read_text()
        )

    def extract(self, raw):
        if (raw.source_name, raw.file_hash, raw.content) != (
            self.raw.source_name,
            self.raw.file_hash,
            self.raw.content,
        ):
            raise ValueError(f"replay input differs from saved native reading: {raw.source_name}")
        return self.document.model_copy(deep=True)


def snapshot_directory(path):
    return {
        str(item.relative_to(path)): hashlib.sha256(item.read_bytes()).hexdigest()
        for item in sorted(path.rglob("*"))
        if item.is_file()
    }


def charge_key(charge):
    return (
        charge.charge_item,
        Decimal(str(charge.amount)),
        charge.currency,
        charge.payment_term,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--replay-from", type=Path, help="replay saved raw and extracted JSON offline"
    )
    args = parser.parse_args()
    source_before = None
    if args.replay_from:
        args.replay_from = args.replay_from.resolve()
        if not (args.replay_from / "summary.json").is_file():
            parser.error("--replay-from must contain a saved verification summary")
        output = args.output_dir.resolve()
        if output == args.replay_from or args.replay_from in output.parents:
            parser.error("replay output must be outside the preserved source directory")
        source_before = snapshot_directory(args.replay_from)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    settings = Settings()
    if not args.replay_from:
        settings.require_llm_credentials()
    default_history_before = (
        settings.existing_bills_path.read_bytes() if settings.existing_bills_path.exists() else None
    )
    samples = json.loads((PROJECT / "tests/fixtures/office_samples.json").read_text())
    results = []
    started = time.monotonic()
    for sample in samples:
        for suffix in ("xlsx", "docx"):
            name = f"{sample['name']}.{suffix}"
            print(f"Starting {name}", flush=True)
            case_dir = args.output_dir / name
            case_dir.mkdir()
            history_path = case_dir / "existing_bills.json"
            history_path.write_text("[]", encoding="utf-8")
            no_ocr = NoOCRClient()
            if args.replay_from:
                agent = InvoiceAgent(
                    settings,
                    DocumentRouter(settings, no_ocr),
                    ReplayExtractor(args.replay_from / name),
                    JsonBillRepository(history_path),
                )
            else:
                agent = build_agent(settings, history_path)
            recording = RecordingExtractor(agent.extractor, case_dir)
            agent.extractor = recording
            agent.router.ocr_client = no_ocr
            expected = agent.normalizer.normalize_document(
                ExtractedDocument.model_validate(sample["document"])
            )
            case_started = time.monotonic()
            operations = agent.process(
                PROJECT / "tests/fixtures/documents" / name,
                accounting_entity=sample["accounting_entity"],
                apply=False,
            )
            (case_dir / "operations.json").write_text(
                serialize_operations(operations, schema_mode="internal"), encoding="utf-8"
            )
            (case_dir / "assignment.json").write_text(
                serialize_operations(operations, schema_mode="assignment"), encoding="utf-8"
            )
            extracted = recording.document
            checks = {
                "charge_count": len(operations) == len(expected.charges),
                "charge_values": Counter(charge_key(op.charge_details) for op in operations)
                == Counter(charge_key(charge) for charge in expected.charges),
                "create": bool(operations)
                and all(op.operation == Operation.CREATE for op in operations),
                "posting_type": bool(operations)
                and all(
                    op.financial_attributes.posting_type == sample["posting_type"]
                    for op in operations
                ),
                "document_fields": bool(operations)
                and all(
                    op.document_info.reference_no == expected.reference_no
                    and op.document_info.document_date == expected.document_date
                    and op.transaction_party.issuer == expected.issuer
                    and op.transaction_party.recipient == expected.recipient
                    for op in operations
                ),
                "shipment_fields": extracted is not None
                and all(
                    getattr(extracted, field) == getattr(expected, field)
                    for field in (
                        "master_bill_of_lading_no",
                        "house_bill_of_lading_no",
                        "booking_no",
                        "container_numbers",
                    )
                ),
                "total": extracted is not None and extracted.total == expected.total,
                "no_ocr": no_ocr.calls == 0,
                "history_unchanged": history_path.read_bytes() == b"[]",
            }
            result = {
                "file": name,
                "passed": all(checks.values()),
                "checks": checks,
                "charges": len(operations),
                "seconds": round(time.monotonic() - case_started, 2),
            }
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
    summary = {
        "mode": "saved_model_replay" if args.replay_from else "live_model",
        "replay_from": str(args.replay_from) if args.replay_from else None,
        "external_model_calls": 0 if args.replay_from else None,
        "model": settings.llm_model_name,
        "provider": settings.llm_provider,
        "thinking": settings.llm_enable_thinking,
        "passed": all(case["passed"] for case in results),
        "seconds": round(time.monotonic() - started, 2),
        "cases": results,
    }
    default_history_after = (
        settings.existing_bills_path.read_bytes() if settings.existing_bills_path.exists() else None
    )
    summary["default_history_unchanged"] = default_history_before == default_history_after
    if args.replay_from:
        source_after = snapshot_directory(args.replay_from)
        summary["source_results_unchanged"] = source_before == source_after
        (args.output_dir / "source_integrity.json").write_text(
            json.dumps({"before": source_before, "after": source_after}, indent=2), encoding="utf-8"
        )
        summary["passed"] = summary["passed"] and summary["source_results_unchanged"]
    summary["passed"] = summary["passed"] and summary["default_history_unchanged"]
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Summary: {args.output_dir / 'summary.json'}", flush=True)
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
