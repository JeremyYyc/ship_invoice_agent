from decimal import Decimal
from pathlib import Path

import pytest

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


def confusion_case(field="reference_no", historical="00yyc", observed="OOyyc"):
    bill = (
        JsonBillRepository(Path("tests/fixtures/existing_bills/exact_duplicate.json"))
        .all()[0]
        .model_copy(deep=True)
    )
    bill.document_info.reference_no = None
    bill.shipment_info = {}
    doc = document()
    doc.reference_no = None
    doc.master_bill_of_lading_no = None
    if field == "reference_no":
        bill.document_info.reference_no = historical
    else:
        bill.shipment_info[field] = [historical] if field == "container_numbers" else historical
    setattr(doc, field, [observed] if field == "container_numbers" else observed)
    return bill, doc


@pytest.mark.parametrize(
    ("historical", "observed"),
    [
        ("00yyc", "OOyyc"),
        ("OOyyc", "00yyc"),
        ("11yyc", "IIyyc"),
        ("IIyyc", "11yyc"),
        ("01OIyyc", "OI01yyc"),
    ],
)
@pytest.mark.parametrize(
    "field",
    [
        "reference_no",
        "master_bill_of_lading_no",
        "house_bill_of_lading_no",
        "booking_no",
        "container_numbers",
    ],
)
def test_confusable_identifier_requires_confirmation(field, historical, observed):
    bill, doc = confusion_case(field, historical, observed)
    decision = BillMatcher([bill]).decide(doc, doc.charges[0])

    assert decision.operation == Operation.PENDING_REVIEW
    assert decision.strategy == "OCR_CONFUSABLE_IDENTIFIER"
    assert decision.matched_bill is None
    assert decision.changed_fields == {}
    assert decision.conflicts == [
        {
            "code": "POSSIBLE_OCR_IDENTIFIER_CONFUSION",
            "field": field,
            "extracted_value": observed,
            "normalized_value": observed.upper(),
            "candidate_bill_id": bill.bill_id,
            "candidate_field": field,
            "candidate_value": historical,
        }
    ]
    assert getattr(doc, field) == ([observed] if field == "container_numbers" else observed)


def test_confusion_candidates_include_all_bills_even_if_one_amount_matches():
    bill, doc = confusion_case()
    other = bill.model_copy(deep=True)
    other.bill_id = "BILL-OTHER"
    other.document_info.reference_no = "0Oyyc"
    other.charge_details.amount = 999
    other.charge_details.currency = "CNY"
    other.transaction_party.issuer = "Other issuer"

    decision = BillMatcher([bill, other]).decide(doc, doc.charges[0])

    assert decision.operation == Operation.PENDING_REVIEW
    assert decision.matched_bill is None
    assert [item["candidate_value"] for item in decision.conflicts] == ["00yyc", "0Oyyc"]
    assert [item["candidate_bill_id"] for item in decision.conflicts] == [
        bill.bill_id,
        other.bill_id,
    ]


@pytest.mark.parametrize(
    "amount, operation", [("65", Operation.NO_CHANGE), ("66", Operation.UPDATE)]
)
@pytest.mark.parametrize(
    "fallback", ["reference_no", "master_bill_of_lading_no", "booking_no", "container_numbers"]
)
def test_confirmed_match_takes_precedence_over_confusion(fallback, amount, operation):
    bill, doc = confusion_case()
    doc.charges[0].amount = Decimal(amount)
    if fallback == "reference_no":
        bill.document_info.reference_no = "OOyyc"
    elif fallback == "container_numbers":
        # The container strategy requires an identical amount.
        bill.shipment_info[fallback] = ["TIIU8975975"]
        doc.container_numbers = ["TIIU8975975"]
        bill.charge_details.amount = float(amount)
        operation = Operation.NO_CHANGE
    else:
        bill.shipment_info[fallback] = "MATCH123"
        setattr(doc, fallback, "MATCH123")

    decision = BillMatcher([bill]).decide(doc, doc.charges[0])

    assert decision.operation == operation
    assert decision.matched_bill.bill_id == bill.bill_id
    assert decision.strategy != "OCR_CONFUSABLE_IDENTIFIER"
    assert decision.conflicts == []


@pytest.mark.parametrize("observed", ["AByyc", "OOyyx", "OOyycc", "Oyyc", "llyyc"])
def test_other_character_differences_do_not_trigger_confusion(observed):
    bill, doc = confusion_case(observed=observed)
    decision = BillMatcher([bill]).decide(doc, doc.charges[0])
    assert decision.operation == Operation.CREATE
    assert decision.conflicts == []


def test_confusion_check_requires_same_charge():
    bill, doc = confusion_case()
    bill.charge_details.charge_item = "Ocean Freight"
    assert BillMatcher([bill]).decide(doc, doc.charges[0]).operation == Operation.CREATE


def test_confusable_identifier_without_history_still_creates():
    _, doc = confusion_case()
    assert BillMatcher([]).decide(doc, doc.charges[0]).operation == Operation.CREATE


def test_confusion_check_compares_master_and_house_bill_numbers():
    bill, doc = confusion_case("master_bill_of_lading_no")
    bill.shipment_info["house_bill_of_lading_no"] = bill.shipment_info.pop(
        "master_bill_of_lading_no"
    )
    decision = BillMatcher([bill]).decide(doc, doc.charges[0])
    assert decision.operation == Operation.PENDING_REVIEW
    assert decision.conflicts[0]["candidate_field"] == "house_bill_of_lading_no"


def test_confusion_check_does_not_mix_unrelated_identifier_fields():
    bill, doc = confusion_case()
    bill.shipment_info["booking_no"] = bill.document_info.reference_no
    bill.document_info.reference_no = None
    assert BillMatcher([bill]).decide(doc, doc.charges[0]).operation == Operation.CREATE


def test_exact_match_wins_even_when_confusable_history_appears_first():
    bill, doc = confusion_case()
    exact = bill.model_copy(deep=True)
    exact.bill_id = "BILL-EXACT"
    exact.document_info.reference_no = doc.reference_no

    decision = BillMatcher([bill, exact]).decide(doc, doc.charges[0])

    assert decision.operation == Operation.NO_CHANGE
    assert decision.matched_bill.bill_id == exact.bill_id
    assert decision.strategy == "REFERENCE_NO_CHARGE_ITEM"
