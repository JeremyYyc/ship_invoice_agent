import json
from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeListChatModel

from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.models import RawDocument


def test_prompt_mode_returns_schema_valid_document():
    response = {
        "document_type": "Debit Note",
        "reference_no": "DN-100",
        "issuer": "Carrier Ltd",
        "recipient": "Importer Ltd",
        "charges": [
            {
                "original_charge_item": "H/C",
                "amount": 65,
                "currency": "usd",
                "confidence": 0.98,
            }
        ],
    }
    extractor = InvoiceExtractor(FakeListChatModel(responses=[json.dumps(response)]))
    document = RawDocument(
        source_path=Path("invoice.pdf"),
        source_name="invoice.pdf",
        source_type="pdf",
        content='{"text":"H/C USD 65"}',
        file_hash="abc",
    )

    extracted = extractor.extract(document)

    assert extracted.reference_no == "DN-100"
    assert extracted.charges[0].amount == 65
    assert extracted.charges[0].currency == "USD"
