from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Operation(StrEnum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    NO_CHANGE = "NO_CHANGE"
    PENDING_REVIEW = "PENDING_REVIEW"


class PostingType(StrEnum):
    AR = "AR"
    AP = "AP"


class DocumentInfo(BaseModel):
    document_type: str | None
    reference_no: str | None
    document_date: str | None


class TransactionParty(BaseModel):
    issuer: str | None
    recipient: str | None


class ChargeDetails(BaseModel):
    charge_item: str | None
    amount: float | None
    currency: str | None
    payment_term: str | None


class FinancialAttributes(BaseModel):
    accounting_entity: str | None
    posting_type: PostingType | None


class AuditTrail(BaseModel):
    source_file: str
    source_page: int | None = None
    original_charge_item: str | None = None
    source_text: str | None = None
    file_hash: str | None = None
    matched_bill_id: str | None = None
    match_strategy: str | None = None
    changed_fields: dict[str, dict[str, Any]] = Field(default_factory=dict)
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    confidence: float = Field(default=0.5, ge=0, le=1)
    ai_reasoning: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class BillOperation(BaseModel):
    operation: Operation
    document_info: DocumentInfo
    transaction_party: TransactionParty
    charge_details: ChargeDetails
    financial_attributes: FinancialAttributes
    audit_trail: AuditTrail


class ExistingBill(BaseModel):
    bill_id: str
    document_info: DocumentInfo
    transaction_party: TransactionParty
    charge_details: ChargeDetails
    shipment_info: dict[str, Any] = Field(default_factory=dict)
    financial_attributes: FinancialAttributes | None = None
