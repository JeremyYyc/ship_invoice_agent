import json
import re
import unicodedata
from pathlib import Path

from ship_invoice_agent.models import ExtractedDocument


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    text = unicodedata.normalize("NFKC", value).casefold()
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text).strip()


def normalize_identifier(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", value).upper())


class NormalizationService:
    def __init__(self, charge_alias_path: Path, party_alias_path: Path):
        self.charge_aliases = self._load(charge_alias_path, {})
        self.party_aliases = self._load(party_alias_path, {})
        self.charge_index = self._build_alias_index(self.charge_aliases)
        self.party_index = self._build_alias_index(self.party_aliases)

    @staticmethod
    def _load(path: Path, default):
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _build_alias_index(mapping: dict[str, list[str]]) -> dict[str, str]:
        index: dict[str, str] = {}
        for canonical, aliases in mapping.items():
            index[normalize_text(canonical)] = canonical
            for alias in aliases:
                index[normalize_text(alias)] = canonical
        return index

    def charge_name(self, value: str) -> str:
        return self.charge_index.get(normalize_text(value), value.strip())

    def party_name(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.party_index.get(normalize_text(value), value.strip())

    def normalize_document(self, document: ExtractedDocument) -> ExtractedDocument:
        result = document.model_copy(deep=True)
        result.reference_no = normalize_identifier(result.reference_no) or result.reference_no
        result.master_bill_of_lading_no = (
            normalize_identifier(result.master_bill_of_lading_no) or result.master_bill_of_lading_no
        )
        result.house_bill_of_lading_no = (
            normalize_identifier(result.house_bill_of_lading_no) or result.house_bill_of_lading_no
        )
        result.booking_no = normalize_identifier(result.booking_no) or result.booking_no
        result.container_numbers = [
            normalize_identifier(value) for value in result.container_numbers if value
        ]
        result.issuer = self.party_name(result.issuer)
        result.recipient = self.party_name(result.recipient)
        result.payee = self.party_name(result.payee)
        for charge in result.charges:
            charge.charge_item = self.charge_name(charge.charge_item or charge.original_charge_item)
            if charge.currency:
                charge.currency = charge.currency.upper().strip()
            if charge.payment_term:
                payment = normalize_text(charge.payment_term)
                if payment in {"pp", "prepaid", "freight prepaid"}:
                    charge.payment_term = "PP"
                elif payment in {"cc", "collect", "freight collect"}:
                    charge.payment_term = "CC"
        return result
