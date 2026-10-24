import json

from pydantic import ValidationError

from ship_invoice_agent.models import (
    AuditTrail,
    BillOperation,
    ChargeDetails,
    DocumentInfo,
    FinancialAttributes,
    Operation,
    PostingType,
    TransactionParty,
)
from ship_invoice_agent.output import AssignmentBillOperation, serialize_operations


def build_operation() -> BillOperation:
    return BillOperation(
        operation=Operation.CREATE,
        document_info=DocumentInfo(
            document_type="Invoice", reference_no="INV1", document_date=None
        ),
        transaction_party=TransactionParty(issuer="A", recipient="B"),
        charge_details=ChargeDetails(
            charge_item="Handling Charge",
            amount=65.0,
            currency="USD",
            payment_term="PP",
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity="A", posting_type=PostingType.AR
        ),
        audit_trail=AuditTrail(
            source_file="invoice.pdf",
            confidence=1,
            ai_reasoning=["no historical bill matched"],
            warnings=["MODEL_CHARGE_NAME_CONFLICT"],
        ),
    )


def test_assignment_output_has_exact_standard_shape():
    parsed = json.loads(serialize_operations([build_operation()]))

    assert isinstance(parsed, list)
    assert set(parsed[0]) == {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }
    assert set(parsed[0]["document_info"]) == {
        "document_type",
        "reference_no",
        "document_date",
    }
    assert set(parsed[0]["transaction_party"]) == {"issuer", "recipient"}
    assert set(parsed[0]["charge_details"]) == {
        "charge_item",
        "amount",
        "currency",
        "payment_term",
    }
    assert set(parsed[0]["financial_attributes"]) == {
        "accounting_entity",
        "posting_type",
    }
    assert set(parsed[0]["audit_trail"]) == {"ai_reasoning"}
    assert isinstance(parsed[0]["charge_details"]["amount"], float)
    assert "source_file" not in parsed[0]["audit_trail"]
    assert "warning: MODEL_CHARGE_NAME_CONFLICT" in parsed[0]["audit_trail"][
        "ai_reasoning"
    ]


def test_internal_output_mode_preserves_diagnostics():
    parsed = json.loads(serialize_operations([build_operation()], schema_mode="internal"))

    assert parsed[0]["audit_trail"]["source_file"] == "invoice.pdf"
    assert parsed[0]["audit_trail"]["warnings"] == ["MODEL_CHARGE_NAME_CONFLICT"]


def test_assignment_schema_rejects_additional_fields():
    payload = json.loads(serialize_operations([build_operation()]))[0]
    payload["audit_trail"]["source_file"] = "invoice.pdf"

    try:
        AssignmentBillOperation.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("assignment schema accepted a non-standard audit field")


def test_amount_is_json_number():
    parsed = json.loads(serialize_operations([build_operation()]))
    assert isinstance(parsed[0]["charge_details"]["amount"], float)
