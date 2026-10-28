import json
from decimal import Decimal
from pathlib import Path

import pytest

from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import ExtractionTimeoutError
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument, Operation, RawDocument
from ship_invoice_agent.output import serialize_operations
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


class ConflictingChargeNameExtractor:
    def extract(self, document):
        return ExtractedDocument(
            document_type="Pre-Alert Email",
            issuer="Pilot Aiship Tech co., Ltd.",
            recipient="Importer Ltd",
            charges=[
                ExtractedCharge(
                    original_charge_item="SHA pay TAR：H/C $50",
                    charge_item="Terminal Handling Charge (THC)",
                    source_text="SHA pay TAR：H/C $50",
                    amount=Decimal("50.00"),
                    currency="USD",
                    confidence=0.99,
                )
            ],
        )


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


@pytest.mark.parametrize(
    ("reference_no", "expected_operation"),
    [("DN-100", Operation.CREATE), ("DC108478", Operation.UPDATE)],
)
def test_workflow_without_apply_preserves_json(tmp_path, reference_no, expected_operation):
    class PreviewExtractor(FakeExtractor):
        def extract(self, document):
            extracted = super().extract(document)
            extracted.reference_no = reference_no
            extracted.document_date = "2026-10-24"
            extracted.recipient = "AEBO TCO IMPORT LLC"
            return extracted

    bills_path = tmp_path / "bills.json"
    original_bytes = Path("tests/fixtures/existing_bills/amount_update.json").read_bytes()
    bills_path.write_bytes(original_bytes)
    settings = Settings(
        existing_bills_path=bills_path,
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
        apply_bill_changes=False,
    )
    repository = JsonBillRepository(bills_path)
    original_bills = repository.all()
    assert len(original_bills) == 1
    assert original_bills[0].charge_details.amount == 60.0
    agent = InvoiceAgent(settings, FakeRouter(), PreviewExtractor(), repository)

    # Omit apply, as the CLI does when --apply is absent and writing is disabled in settings.
    operations = agent.process(Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.")

    assert len(operations) == 1
    assert operations[0].operation == expected_operation
    assert operations[0].charge_details.amount == 65.0
    if expected_operation == Operation.UPDATE:
        assert operations[0].audit_trail.matched_bill_id == original_bills[0].bill_id
        assert operations[0].audit_trail.changed_fields == {"amount": {"old": 60.0, "new": 65.0}}
    else:
        assert operations[0].audit_trail.matched_bill_id is None
        assert operations[0].audit_trail.changed_fields == {}
    assert bills_path.read_bytes() == original_bytes
    persisted_bills = JsonBillRepository(bills_path).all()
    assert len(persisted_bills) == len(original_bills) == 1
    assert persisted_bills[0].charge_details.amount == 60.0
    assert persisted_bills == original_bills


def test_workflow_applies_update_and_persists_existing_bill(tmp_path):
    class UpdatedAmountExtractor(FakeExtractor):
        def extract(self, document):
            extracted = super().extract(document)
            extracted.reference_no = "DC108478"
            extracted.document_date = "2026-10-24"
            extracted.recipient = "AEBO TCO IMPORT LLC"
            return extracted

    bills_path = tmp_path / "bills.json"
    bills_path.write_bytes(Path("tests/fixtures/existing_bills/amount_update.json").read_bytes())
    settings = Settings(
        existing_bills_path=bills_path,
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
        apply_bill_changes=False,
    )
    repository = JsonBillRepository(bills_path)
    original_bills = repository.all()
    assert len(original_bills) == 1
    original_bill_id = original_bills[0].bill_id
    assert original_bills[0].charge_details.amount == 60.0
    agent = InvoiceAgent(settings, FakeRouter(), UpdatedAmountExtractor(), repository)

    operations = agent.process(
        Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.", apply=True
    )

    assert len(operations) == 1
    assert operations[0].operation == Operation.UPDATE
    assert operations[0].audit_trail.matched_bill_id == original_bill_id
    assert operations[0].audit_trail.changed_fields == {"amount": {"old": 60.0, "new": 65.0}}
    # Reload from disk to verify persistence independently of the original repository's memory.
    persisted_bills = JsonBillRepository(bills_path).all()
    assert len(persisted_bills) == len(original_bills) == 1
    assert persisted_bills[0].bill_id == original_bill_id
    assert persisted_bills[0].charge_details.amount == 65.0


def test_workflow_create_update_no_change_across_repository_reloads(tmp_path):
    class AmountExtractor(FakeExtractor):
        def __init__(self, amount):
            self.amount = Decimal(amount)

        def extract(self, document):
            extracted = super().extract(document)
            extracted.charges[0].amount = self.amount
            return extracted

    bills_path = tmp_path / "bills.json"
    bills_path.write_text("[]", encoding="utf-8")
    settings = Settings(
        existing_bills_path=bills_path,
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
        apply_bill_changes=False,
    )

    def process_amount(amount):
        # Fresh instances ensure each decision depends on persisted history, not batch memory.
        repository = JsonBillRepository(bills_path)
        agent = InvoiceAgent(settings, FakeRouter(), AmountExtractor(amount), repository)
        operations = agent.process(
            Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.", apply=True
        )
        assert len(operations) == 1
        persisted_bills = JsonBillRepository(bills_path).all()
        assert len(persisted_bills) == 1
        return operations[0], persisted_bills[0]

    created, initial_bill = process_amount("65.00")
    assert created.operation == Operation.CREATE
    assert created.audit_trail.matched_bill_id is None
    assert initial_bill.charge_details.amount == 65.0
    bill_id = initial_bill.bill_id

    updated, updated_bill = process_amount("75.00")
    assert updated.operation == Operation.UPDATE
    assert updated.audit_trail.matched_bill_id == bill_id
    assert updated.audit_trail.changed_fields == {"amount": {"old": 65.0, "new": 75.0}}
    assert updated_bill.bill_id == bill_id
    assert updated_bill.charge_details.amount == 75.0

    repeated, unchanged_bill = process_amount("75.00")
    assert repeated.operation == Operation.NO_CHANGE
    assert repeated.audit_trail.matched_bill_id == bill_id
    assert repeated.audit_trail.changed_fields == {}
    assert "DUPLICATE_IN_CURRENT_BATCH" not in repeated.audit_trail.warnings
    assert unchanged_bill.bill_id == bill_id
    assert unchanged_bill.charge_details.amount == 75.0
    assert unchanged_bill == updated_bill


def test_workflow_currency_conflict_preserves_json_even_with_apply(tmp_path):
    class CurrencyConflictExtractor(FakeExtractor):
        def extract(self, document):
            extracted = super().extract(document)
            extracted.reference_no = "DC108478"
            extracted.document_date = "2026-10-24"
            extracted.recipient = "AEBO TCO IMPORT LLC"
            extracted.charges[0].amount = Decimal("75.00")
            return extracted

    bills_path = tmp_path / "bills.json"
    original_bytes = Path("tests/fixtures/existing_bills/currency_conflict.json").read_bytes()
    bills_path.write_bytes(original_bytes)
    settings = Settings(
        existing_bills_path=bills_path,
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
        apply_bill_changes=False,
    )
    repository = JsonBillRepository(bills_path)
    original_bills = repository.all()
    assert len(original_bills) == 1
    assert original_bills[0].charge_details.amount == 65.0
    assert original_bills[0].charge_details.currency == "CNY"
    agent = InvoiceAgent(settings, FakeRouter(), CurrencyConflictExtractor(), repository)

    operations = agent.process(
        Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.", apply=True
    )

    assert len(operations) == 1
    assert operations[0].operation == Operation.PENDING_REVIEW
    assert operations[0].charge_details.amount == 75.0
    assert operations[0].charge_details.currency == "USD"
    assert operations[0].audit_trail.matched_bill_id == original_bills[0].bill_id
    assert operations[0].audit_trail.conflicts == [
        {"field": "currency", "existing_value": "CNY", "extracted_value": "USD"}
    ]
    assert operations[0].audit_trail.changed_fields == {}
    assert bills_path.read_bytes() == original_bytes
    persisted_bills = JsonBillRepository(bills_path).all()
    assert len(persisted_bills) == len(original_bills) == 1
    assert persisted_bills[0].charge_details.amount == 65.0
    assert persisted_bills[0].charge_details.currency == "CNY"
    assert persisted_bills == original_bills


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


def test_workflow_reviews_model_charge_name_conflict(tmp_path):
    settings = Settings(
        existing_bills_path=tmp_path / "bills.json",
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
        charge_alias_conflict_policy="review",
    )
    repository = JsonBillRepository(settings.existing_bills_path)
    agent = InvoiceAgent(settings, FakeRouter(), ConflictingChargeNameExtractor(), repository)

    operations = agent.process(Path("invoice.eml"), accounting_entity="Pilot Aiship Tech co., Ltd.")

    assert len(operations) == 1
    assert operations[0].operation == Operation.PENDING_REVIEW
    assert operations[0].charge_details.charge_item == "Handling Charge"
    assert "MODEL_CHARGE_NAME_CONFLICT" in operations[0].audit_trail.warnings
    assert "MODEL_CHARGE_NAME_OVERRIDDEN" in operations[0].audit_trail.warnings
    assert any(
        "deterministic evidence took precedence" in item
        for item in operations[0].audit_trail.ai_reasoning
    )


@pytest.mark.parametrize("observed", ["OOyyc", " ＯＯ-yyc "])
def test_workflow_preserves_confusion_evidence_and_never_applies_candidates(tmp_path, observed):
    class ConfusedIdentifierExtractor(FakeExtractor):
        def extract(self, document):
            extracted = super().extract(document)
            extracted.reference_no = observed
            return extracted

    settings = Settings(
        existing_bills_path=tmp_path / "bills.json",
        charge_aliases_path=Path("data/charge_aliases.json"),
        party_aliases_path=Path("data/party_aliases.json"),
        currencies_path=Path("data/currencies.json"),
    )
    repository = JsonBillRepository(settings.existing_bills_path)
    existing = (
        JsonBillRepository(Path("tests/fixtures/existing_bills/exact_duplicate.json"))
        .all()[0]
        .model_copy(deep=True)
    )
    existing.document_info.reference_no = "00yyc"
    repository.upsert(existing)
    repository.save()
    original_bytes = repository.path.read_bytes()
    agent = InvoiceAgent(settings, FakeRouter(), ConfusedIdentifierExtractor(), repository)

    # Repeated input must not downgrade a review decision to NO_CHANGE.
    for _ in range(2):
        operations = agent.process(
            Path("invoice.pdf"), accounting_entity="Pilot Aiship Tech co., Ltd.", apply=True
        )
        assert len(operations) == 1
        operation = operations[0]
        assert operation.operation == Operation.PENDING_REVIEW
        assert operation.document_info.reference_no == "OOYYC"
        assert operation.audit_trail.match_strategy == "OCR_CONFUSABLE_IDENTIFIER"
        assert operation.audit_trail.matched_bill_id is None
        assert operation.audit_trail.changed_fields == {}
        conflict = operation.audit_trail.conflicts[0]
        assert conflict["extracted_value"] == observed
        assert conflict["normalized_value"] == "OOYYC"
        assert conflict["candidate_value"] == "00yyc"
        assert conflict["candidate_bill_id"] == existing.bill_id

        public = json.loads(serialize_operations(operations))[0]
        assert public["operation"] == "PENDING_REVIEW"
        assert any(
            f"原始识别值为 {observed}，候选历史单据编号为 00yyc" in reason
            and existing.bill_id in reason
            for reason in public["audit_trail"]["ai_reasoning"]
        )
        internal = json.loads(serialize_operations(operations, schema_mode="internal"))[0]
        assert internal["audit_trail"]["conflicts"][0] == conflict
        assert repository.path.read_bytes() == original_bytes
        assert len(repository.all()) == 1
        assert repository.all()[0].document_info.reference_no == "00yyc"
