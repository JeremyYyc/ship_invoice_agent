import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.contracts import EXTRACTION_SCHEMA_VERSION
from ship_invoice_agent.evaluation import evaluate_output
from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.llm import create_chat_model
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import RawDocument
from ship_invoice_agent.ocr import OCRClient
from ship_invoice_agent.output import serialize_operations
from ship_invoice_agent.parsers import DocumentRouter
from ship_invoice_agent.workflow import InvoiceAgent


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def build_agent(settings: Settings, existing_bills: Path | None = None) -> InvoiceAgent:
    ocr = OCRClient(settings)
    router = DocumentRouter(settings, ocr)
    llm = create_chat_model(settings)
    extractor = InvoiceExtractor(
        llm,
        Path("prompts/extract_document.md"),
        structured_output_mode=settings.llm_structured_output_mode,
        request_timeout_seconds=settings.llm_timeout_seconds,
        total_time_budget_seconds=settings.llm_total_time_budget_seconds,
        max_retries=settings.llm_max_retries,
    )
    repository = JsonBillRepository(existing_bills or settings.existing_bills_path)
    return InvoiceAgent(settings, router, extractor, repository)


def process_command(args: argparse.Namespace, settings: Settings) -> int:
    agent = build_agent(settings, args.existing_bills)
    operations = agent.process(
        args.input,
        file_url=args.file_url,
        accounting_entity=args.accounting_entity,
        apply=args.apply,
    )
    sys.stdout.write(
        serialize_operations(
            operations,
            settings.json_output_indent,
            settings.output_schema_mode,
        )
        + "\n"
    )
    return 0


def check_model_command(settings: Settings, report_dir: Path | None = None) -> int:
    if report_dir:
        report_dir.mkdir(parents=True, exist_ok=False)
    llm = create_chat_model(settings)
    content = (
        "Invoice SCHEMA-0001\nIssuer: Schema Probe Vendor\nRecipient: Schema Probe Buyer\n"
        "Handling Fee | 1 | 1.25 | USD | PP | 1.25\nTotal USD 1.25"
    )
    raw = RawDocument(
        source_path=Path("schema-probe.txt"),
        source_name="schema-probe.txt",
        source_type="synthetic",
        content=content,
        file_hash=hashlib.sha256(content.encode()).hexdigest(),
    )
    extractor = InvoiceExtractor(
        llm,
        Path("prompts/extract_document.md"),
        structured_output_mode=settings.llm_structured_output_mode,
        request_timeout_seconds=settings.llm_timeout_seconds,
        total_time_budget_seconds=settings.llm_total_time_budget_seconds,
        max_retries=settings.llm_max_retries,
    )
    documents = extractor.extract_documents(raw)
    if report_dir:
        (report_dir / "source.json").write_text(raw.model_dump_json(indent=2), encoding="utf-8")
        if extractor.last_raw_response:
            (report_dir / "wire_response.json").write_text(
                extractor.last_raw_response, encoding="utf-8"
            )
        (report_dir / "documents.json").write_text(
            json.dumps([d.model_dump(mode="json") for d in documents], indent=2), encoding="utf-8"
        )
    errors = []
    if len(documents) != 1:
        errors.append("PROBE_DOCUMENT_COUNT")
    else:
        document = documents[0]
        errors.extend(document.review_reasons)
        if document.reference_no != "SCHEMA-0001":
            errors.append("PROBE_REFERENCE")
        if len(document.charges) != 1:
            errors.append("PROBE_CHARGE_COUNT")
        else:
            errors.extend(document.charges[0].review_reasons)
            if document.charges[0].amount != 1.25 or document.charges[0].currency != "USD":
                errors.append("PROBE_AMOUNT_CURRENCY")
    sys.stdout.write(
        json.dumps(
            {
                "provider": settings.llm_provider,
                "model": settings.llm_model_name,
                "enable_thinking": settings.llm_enable_thinking,
                "connection": "ok",
                "json_output": "ok",
                "schema_version": EXTRACTION_SCHEMA_VERSION,
                "structured_output_mode": settings.llm_structured_output_mode,
                "evidence_validation": "failed" if errors else "ok",
                "probe_errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 1 if errors else 0


def evaluate_command(args: argparse.Namespace) -> int:
    metrics = evaluate_output(args.actual, args.golden)
    sys.stdout.write(json.dumps(metrics.as_dict(), ensure_ascii=False, indent=2) + "\n")
    return 1 if metrics.invalid_json else 0


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ship-invoice-agent")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="process one document")
    process.add_argument("--input", type=Path, required=True)
    process.add_argument("--file-url")
    process.add_argument("--accounting-entity")
    process.add_argument("--existing-bills", type=Path)
    process.add_argument("--apply", action="store_true", default=None)

    check_model = subparsers.add_parser(
        "check-model", help="verify extraction and evidence contract"
    )
    check_model.add_argument("--report-dir", type=Path)
    evaluate = subparsers.add_parser("evaluate", help="compare output JSON with golden data")
    evaluate.add_argument("--actual", type=Path, required=True)
    evaluate.add_argument("--golden", type=Path, required=True)
    return parser


def main() -> int:
    parser = make_parser()
    args = parser.parse_args()
    try:
        settings = Settings()
        configure_logging(settings.log_level)
        if args.command == "process":
            return process_command(args, settings)
        if args.command == "check-model":
            return check_model_command(settings, args.report_dir)
        if args.command == "evaluate":
            return evaluate_command(args)
        parser.error(f"unknown command: {args.command}")
    except Exception as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
