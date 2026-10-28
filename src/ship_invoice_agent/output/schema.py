from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field

from ship_invoice_agent.models import BillOperation, Operation, PostingType
from ship_invoice_agent.output.reasoning_zh import build_assignment_reasoning


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
    amount: Decimal | None
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
            audit_trail=AssignmentAuditTrail(
                ai_reasoning=build_assignment_reasoning(operation.audit_trail)
            ),
        )
