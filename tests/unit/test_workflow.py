from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import ExtractionTimeoutError
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument, Operation, RawDocument
from ship_invoice_agent.workflow import InvoiceAgent


class FakeRouter:
    def parse(self, path, *, file_url=None):
        return [
            RawDocument(
                source_path=path,
                source_name=path.name,
                source_type="pdf",
                content="recognized text",
                file_hash="abc123",
            )
        ]


class FakeExtractor:
    def extract(self, document):
        return ExtractedDocument(
            document_type="Debit Note",
            reference_no="DN-100",
            document_date="2026-10-20",
            issuer="Pilot Aiship Tech co., Ltd.",
            recipient="Importer Ltd",
            charges=[
                ExtractedCharge(
                    original_charge_item="H/C",
                    amount=Decimal("65.00"),
                    currency="USD",
                    confidence=0.99,
                )
            ],
        )


class FailingExtractor:
    def extract(self, document):
        raise ExtractionTimeoutError("simulated exhausted budget")


def test_workflow_normalizes_classifies_and_creates(tmp_path):
    settings = Settings(
        existing_bills_path=tmp_path / "bills.json",
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
    )
    repository = JsonBillRepository(settings.existing_bills_path)
    agent = InvoiceAgent(settings, FakeRouter(), FakeExtractor(), repository)

    operations = agent.process(
        Path("invoice.pdf"), accounting_entity="湃乐多（上海）航运科技有限公司"
    )

    assert len(operations) == 1
    assert operations[0].operation == Operation.CREATE
    assert operations[0].charge_details.charge_item == "Handling Charge"
    assert operations[0].financial_attributes.posting_type == "AR"
    assert operations[0].financial_attributes.accounting_entity == "Pilot Aiship Tech co., Ltd."
    assert operations[0].charge_details.amount == 65.0


def test_workflow_turns_extraction_timeout_into_pending_review(tmp_path):
    settings = Settings(
        existing_bills_path=tmp_path / "bills.json",
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
    )
    repository = JsonBillRepository(settings.existing_bills_path)
    agent = InvoiceAgent(settings, FakeRouter(), FailingExtractor(), repository)

    operations = agent.process(Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.")

    assert len(operations) == 1
    assert operations[0].operation == Operation.PENDING_REVIEW
    assert "LLM_EXTRACTION_FAILED" in operations[0].audit_trail.warnings
    assert "LLM_TIME_BUDGET_EXHAUSTED" in operations[0].audit_trail.warnings
