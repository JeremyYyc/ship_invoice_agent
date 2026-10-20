from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.matching import BillMatcher, JsonBillRepository
from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument, Operation


def document(amount: str = "65.00", currency: str = "USD"):
    return ExtractedDocument(
        document_type="Debit Note",
        reference_no="DC108478",
        document_date="2026-10-24",
        issuer="Pilot Aiship Tech co., Ltd.",
        recipient="AEBO TCO IMPORT LLC",
        master_bill_of_lading_no="MAEU275752922",
        charges=[
            ExtractedCharge(
                original_charge_item="HANDLING CHARGE",
                charge_item="Handling Charge",
                amount=Decimal(amount),
                currency=currency,
                confidence=0.98,
            )
        ],
    )


def test_empty_repository_creates():
    matcher = BillMatcher([])
    doc = document()
    assert matcher.decide(doc, doc.charges[0]).operation == Operation.CREATE


def test_exact_duplicate_no_change():
    repository = JsonBillRepository(Path("tests/fixtures/existing_bills/exact_duplicate.json"))
    doc = document()
    decision = BillMatcher(repository.all()).decide(doc, doc.charges[0])
    assert decision.operation == Operation.NO_CHANGE
    assert decision.matched_bill.bill_id == "BILL-000001"


def test_unique_amount_change_updates():
    repository = JsonBillRepository(Path("tests/fixtures/existing_bills/amount_update.json"))
    doc = document()
    decision = BillMatcher(repository.all()).decide(doc, doc.charges[0])
    assert decision.operation == Operation.UPDATE
    assert decision.changed_fields["amount"] == {"old": 60.0, "new": 65.0}


def test_currency_conflict_requires_review():
    repository = JsonBillRepository(Path("tests/fixtures/existing_bills/currency_conflict.json"))
    doc = document(currency="USD")
    decision = BillMatcher(repository.all()).decide(doc, doc.charges[0])
    assert decision.operation == Operation.PENDING_REVIEW
    assert decision.conflicts[0]["field"] == "currency"
