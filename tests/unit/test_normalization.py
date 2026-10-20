from decimal import Decimal
from pathlib import Path

from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument
from ship_invoice_agent.normalization import NormalizationService, normalize_identifier


def test_normalizes_charge_alias_and_identifiers():
    service = NormalizationService(
        Path("data/charge_aliases.json"), Path("data/party_aliases.json")
    )
    document = ExtractedDocument(
        reference_no="DC-108478",
        issuer="湃乐多（上海）航运科技有限公司",
        charges=[
            ExtractedCharge(
                original_charge_item="H/D Fee",
                amount=Decimal("65.00"),
                currency="usd",
                payment_term="prepaid",
                confidence=0.95,
            )
        ],
    )

    normalized = service.normalize_document(document)

    assert normalized.reference_no == "DC108478"
    assert normalized.issuer == "Pilot Aiship Tech co., Ltd."
    assert normalized.charges[0].charge_item == "Handling Charge"
    assert normalized.charges[0].currency == "USD"
    assert normalized.charges[0].payment_term == "PP"
    assert normalize_identifier("OOLU-4055 331281") == "OOLU4055331281"
