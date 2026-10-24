import json
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from ship_invoice_agent.exceptions import ExtractionError, ExtractionTimeoutError
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


class AlwaysFailingModel:
    def __init__(self):
        self.calls = 0

    def invoke(self, messages):
        self.calls += 1
        raise TimeoutError("simulated provider timeout")


class InvalidResponseModel:
    def invoke(self, messages):
        return type("Response", (), {"content": "not-json"})()


def test_extractor_retries_only_once():
    model = AlwaysFailingModel()
    extractor = InvoiceExtractor(
        model,
        request_timeout_seconds=60,
        total_time_budget_seconds=130,
        max_retries=1,
    )
    document = RawDocument(
        source_path=Path("invoice.pdf"),
        source_name="invoice.pdf",
        source_type="pdf",
        content="H/C USD 65",
        file_hash="abc",
    )

    with pytest.raises(ExtractionTimeoutError, match="after 2 attempt"):
        extractor.extract(document)

    assert model.calls == 2


def test_extractor_does_not_retry_when_budget_cannot_cover_another_request(monkeypatch):
    model = AlwaysFailingModel()
    clock = iter([0.0, 0.0, 71.0, 71.0])
    monkeypatch.setattr("ship_invoice_agent.extraction.chain.time.monotonic", lambda: next(clock))
    extractor = InvoiceExtractor(
        model,
        request_timeout_seconds=60,
        total_time_budget_seconds=120,
        max_retries=1,
    )
    document = RawDocument(
        source_path=Path("invoice.pdf"),
        source_name="invoice.pdf",
        source_type="pdf",
        content="H/C USD 65",
        file_hash="abc",
    )

    with pytest.raises(ExtractionTimeoutError, match="after 1 attempt"):
        extractor.extract(document)

    assert model.calls == 1


def test_schema_failure_is_not_misclassified_as_timeout():
    extractor = InvoiceExtractor(
        InvalidResponseModel(),
        request_timeout_seconds=60,
        total_time_budget_seconds=130,
        max_retries=1,
    )
    document = RawDocument(
        source_path=Path("invoice.pdf"),
        source_name="invoice.pdf",
        source_type="pdf",
        content="H/C USD 65",
        file_hash="abc",
    )

    with pytest.raises(ExtractionError) as raised:
        extractor.extract(document)

    assert not isinstance(raised.value, ExtractionTimeoutError)
