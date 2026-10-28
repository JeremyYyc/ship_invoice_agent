import json
from pathlib import Path

from ship_invoice_agent.models import ExtractedDocument, PostingType
from ship_invoice_agent.normalization import normalize_text


def classify_posting(
    document: ExtractedDocument,
    accounting_entity: str | None,
    accounting_entities_path: Path | None = None,
) -> tuple[PostingType | None, list[str]]:
    if not accounting_entity:
        return None, ["ACCOUNTING_ENTITY_NOT_PROVIDED"]
    canonical_entity = resolve_accounting_entity(accounting_entity, accounting_entities_path)
    entity = normalize_text(canonical_entity)
    issuer = normalize_text(document.issuer)
    recipient = normalize_text(document.recipient)
    payee = normalize_text(document.payee)
    payer = normalize_text(document.payer)
    receivable = bool(entity and entity in {issuer, payee})
    payable = bool(entity and entity in {recipient, payer})
    if receivable and payable:
        return None, ["ACCOUNTING_ENTITY_ROLE_UNRESOLVED"]
    if receivable:
        return PostingType.AR, ["accounting entity is issuer/payee"]
    if payable:
        return PostingType.AP, ["accounting entity is recipient"]
    return None, ["ACCOUNTING_ENTITY_ROLE_UNRESOLVED"]


def resolve_accounting_entity(value: str, path: Path | None) -> str:
    if not path or not path.exists():
        return value.strip()
    records = json.loads(path.read_text(encoding="utf-8"))
    normalized = normalize_text(value)
    for record in records:
        names = [record.get("canonical_name"), *record.get("aliases", [])]
        if normalized in {normalize_text(name) for name in names if name}:
            return record["canonical_name"]
    return value.strip()
