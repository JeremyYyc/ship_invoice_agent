from pydantic import BaseModel, ConfigDict, Field

from ship_invoice_agent.models import BillOperation, Operation, PostingType


class AssignmentDocumentInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_type: str | None
    reference_no: str | None
    document_date: str | None


class AssignmentTransactionParty(BaseModel):
    model_config = ConfigDict(extra="forbid")

    issuer: str | None
    recipient: str | None


class AssignmentChargeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    charge_item: str | None
    amount: float | None
    currency: str | None
    payment_term: str | None


class AssignmentFinancialAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accounting_entity: str | None
    posting_type: PostingType | None


class AssignmentAuditTrail(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_reasoning: list[str] = Field(default_factory=list)


class AssignmentBillOperation(BaseModel):
    """Strict public DTO matching the assignment's standard output contract."""

    model_config = ConfigDict(extra="forbid")

    operation: Operation
    document_info: AssignmentDocumentInfo
    transaction_party: AssignmentTransactionParty
    charge_details: AssignmentChargeDetails
    financial_attributes: AssignmentFinancialAttributes
    audit_trail: AssignmentAuditTrail

    @classmethod
    def from_internal(cls, operation: BillOperation) -> "AssignmentBillOperation":
        reasoning = list(operation.audit_trail.ai_reasoning)
        reasoning.extend(
            f"changed {field}: {values.get('old')} -> {values.get('new')}"
            for field, values in operation.audit_trail.changed_fields.items()
        )
        reasoning.extend(
            f"conflict {item.get('field', 'unknown')}: "
            f"{item.get('existing_value')} != {item.get('extracted_value')}"
            for item in operation.audit_trail.conflicts
        )
        reasoning.extend(f"warning: {warning}" for warning in operation.audit_trail.warnings)
        reasoning = list(dict.fromkeys(reasoning))

        return cls(
            operation=operation.operation,
            document_info=AssignmentDocumentInfo(
                document_type=operation.document_info.document_type,
                reference_no=operation.document_info.reference_no,
                document_date=operation.document_info.document_date,
            ),
            transaction_party=AssignmentTransactionParty(
                issuer=operation.transaction_party.issuer,
                recipient=operation.transaction_party.recipient,
            ),
            charge_details=AssignmentChargeDetails(
                charge_item=operation.charge_details.charge_item,
                amount=operation.charge_details.amount,
                currency=operation.charge_details.currency,
                payment_term=operation.charge_details.payment_term,
            ),
            financial_attributes=AssignmentFinancialAttributes(
                accounting_entity=operation.financial_attributes.accounting_entity,
                posting_type=operation.financial_attributes.posting_type,
            ),
            audit_trail=AssignmentAuditTrail(ai_reasoning=reasoning),
        )
