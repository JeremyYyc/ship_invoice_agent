import json
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.models import ExtractedDocument


@dataclass
class ValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def requires_review(self) -> bool:
        return bool(self.errors)


def load_currency_codes(path: Path) -> set[str]:
    if not path.exists():
        return {"USD", "CNY", "EUR", "GBP", "JPY", "HKD", "SGD"}
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        return {str(key).upper() for key in data}
    return {str(item).upper() for item in data}


def validate_document(document: ExtractedDocument, currencies_path: Path) -> ValidationResult:
    result = ValidationResult()
    currencies = load_currency_codes(currencies_path)
    if not document.charges:
        result.errors.append("NO_CHARGES_EXTRACTED")
    for index, charge in enumerate(document.charges):
        if charge.amount < Decimal("0"):
            result.errors.append(f"NEGATIVE_AMOUNT:{index}")
        if charge.currency and charge.currency not in currencies:
            result.errors.append(f"INVALID_CURRENCY:{index}:{charge.currency}")
        source = charge.source_text or ""
        if any(token in source for token in (".O", "O.", "I0", "l0")):
            result.warnings.append(f"POSSIBLE_OCR_CHARACTER_CONFUSION:{index}")
    if document.total is not None and document.charges:
        calculated = sum((charge.amount for charge in document.charges), Decimal("0"))
        if abs(calculated - document.total) > Decimal("0.01"):
            result.errors.append(f"TOTAL_MISMATCH:{calculated}:{document.total}")
    return result
