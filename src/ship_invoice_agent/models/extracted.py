from decimal import Decimal

from pydantic import BaseModel, Field, field_validator


class ExtractedCharge(BaseModel):
    original_charge_item: str
    charge_item: str | None = None
    quantity: Decimal | None = None
    unit_price: Decimal | None = None
    amount: Decimal
    currency: str | None = None
    payment_term: str | None = None
    source_text: str | None = None
    source_page: int | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, value: str | None) -> str | None:
        return value.upper().strip() if value else value


class ExtractedDocument(BaseModel):
    document_type: str | None = None
    reference_no: str | None = None
    document_date: str | None = None
    issuer: str | None = None
    recipient: str | None = None
    payee: str | None = None
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
