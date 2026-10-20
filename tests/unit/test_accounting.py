from pathlib import Path

from ship_invoice_agent.accounting import classify_posting
from ship_invoice_agent.models import ExtractedDocument, PostingType


def test_accounting_entity_alias_is_resolved_from_json():
    document = ExtractedDocument(
        issuer="Pilot Aiship Tech co., Ltd.",
        recipient="Importer Ltd",
    )

    posting_type, reasoning = classify_posting(
        document,
        "湃乐多（上海）航运科技有限公司",
        Path("data/accounting_entities.json"),
    )

    assert posting_type == PostingType.AR
    assert reasoning == ["accounting entity is issuer/payee"]
