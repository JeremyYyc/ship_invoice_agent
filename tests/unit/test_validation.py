from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument
from ship_invoice_agent.validation import validate_document


def test_total_mismatch_requires_review():
    document = ExtractedDocument(
        total=Decimal("100"),
        charges=[
            ExtractedCharge(
                original_charge_item="Fee",
                amount=Decimal("90"),
                currency="USD",
            )
        ],
    )
    result = validate_document(document, Path("data/currencies.json"))
    assert result.requires_review
    assert result.errors[0].startswith("TOTAL_MISMATCH")
