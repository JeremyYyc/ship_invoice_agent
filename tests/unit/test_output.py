import json

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
from ship_invoice_agent.output import serialize_operations


def test_amount_is_json_number():
    operation = BillOperation(
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
        audit_trail=AuditTrail(source_file="invoice.pdf", confidence=1),
    )

    parsed = json.loads(serialize_operations([operation]))
    assert isinstance(parsed, list)
    assert isinstance(parsed[0]["charge_details"]["amount"], float)
