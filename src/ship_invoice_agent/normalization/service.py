import json
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from ship_invoice_agent.models import ExtractedDocument
from ship_invoice_agent.normalization.charge_alias_resolver import (
    ChargeAliasResolution,
    ChargeAliasResolver,
    normalize_alias_text,
)


def normalize_text(value: str | None) -> str:
    return normalize_alias_text(value)


def normalize_identifier(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"[^A-Z0-9]", "", unicodedata.normalize("NFKC", value).upper())


class NormalizationService:
    def __init__(
        self,
        charge_alias_path: Path,
        party_alias_path: Path,
        charge_alias_conflict_policy: Literal["review", "override"] = "review",
    ):
        self.charge_aliases = self._load(charge_alias_path, {})
        self.party_aliases = self._load(party_alias_path, {})
        self.charge_resolver = ChargeAliasResolver(
            self.charge_aliases, charge_alias_conflict_policy
        )
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
        return self.charge_resolver.resolve_name(value)

    def party_name(self, value: str | None) -> str | None:
        if not value:
            return None
        return self.party_index.get(normalize_text(value), value.strip())

    def normalize_document(self, document: ExtractedDocument) -> ExtractedDocument:
        return self.normalize_document_with_audit(document).document

    def normalize_document_with_audit(self, document: ExtractedDocument) -> "NormalizedDocument":
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
        resolutions: list[ChargeAliasResolution] = []
        for charge in result.charges:
            resolution = self.charge_resolver.resolve(
                model_name=charge.charge_item,
                original_name=charge.original_charge_item or "",
                source_text=charge.source_text,
            )
            charge.charge_item = resolution.canonical_name
            resolutions.append(resolution)
            if charge.currency:
                charge.currency = charge.currency.upper().strip()
            if charge.payment_term:
                payment = normalize_text(charge.payment_term)
                if payment in {"pp", "prepaid", "freight prepaid"}:
                    charge.payment_term = "PP"
                elif payment in {"cc", "collect", "freight collect"}:
                    charge.payment_term = "CC"
        return NormalizedDocument(document=result, charge_resolutions=resolutions)


@dataclass(frozen=True)
class NormalizedDocument:
    document: ExtractedDocument
    charge_resolutions: list[ChargeAliasResolution]
