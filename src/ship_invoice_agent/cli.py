import argparse
import json
import logging
import sys
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.evaluation import evaluate_output
from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.llm import create_chat_model
from ship_invoice_agent.matching import JsonBillRepository
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
    sys.stdout.write(serialize_operations(operations, settings.json_output_indent) + "\n")
    return 0


def check_model_command(settings: Settings) -> int:
    llm = create_chat_model(settings)
    response = llm.invoke('Return exactly this JSON object: {"status":"ok","amount":1.25}')
    content = str(response.content).strip().removeprefix("```json").removesuffix("```").strip()
    parsed = json.loads(content)
    if parsed != {"status": "ok", "amount": 1.25}:
        raise ValueError(f"model JSON conformance failed: {parsed}")
    sys.stdout.write(
        json.dumps(
            {
                "provider": settings.llm_provider,
                "model": settings.llm_model_name,
                "connection": "ok",
                "json_output": "ok",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )
    return 0


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

    subparsers.add_parser("check-model", help="verify configured model JSON behavior")
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
            return check_model_command(settings)
        if args.command == "evaluate":
            return evaluate_command(args)
        parser.error(f"unknown command: {args.command}")
    except Exception as exc:
        logging.getLogger(__name__).error("%s", exc)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
