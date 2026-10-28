from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class ExtractedCharge(BaseModel):
    original_charge_item: str | None
    charge_item: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal | None
    currency: str | None = None
    payment_term: str | None = None
    source_text: str | None = None
    source_page: int | None = None
    confidence: float | None = Field(default=0.5, ge=0, le=1)
    evidence: dict[str, list[str]] = Field(default_factory=dict)
    review_reasons: list[str] = Field(default_factory=list)
    explicit_update_fields: list[str] = Field(default_factory=list)
    container_numbers: list[str] = Field(default_factory=list)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper().strip() if value else value


class ExtractedDocument(BaseModel):
    extraction_schema_version: str | None = None
    review_reasons: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    totals: list[dict] = Field(default_factory=list)
    observations: list[dict] = Field(default_factory=list)
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    issuer: str | None = None
    recipient: str | None = None
    payee: str | None = None
    payer: str | None = None
    master_bill_of_lading_no: str | None = None
    house_bill_of_lading_no: str | None = None
    booking_no: str | None = None
    container_numbers: list[str] = Field(default_factory=list)
    vessel: str | None = None
    voyage: str | None = None
    port_of_loading: str | None = None
    port_of_discharge: str | None = None
    eta: str | None = None
    etd: str | None = None
    subtotal: Decimal | None = None
    tax: Decimal | None = None
    total: Decimal | None = None
    charges: list[ExtractedCharge] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
