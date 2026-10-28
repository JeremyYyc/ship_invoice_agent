"""Regression coverage for fact/citation binding at the generation boundary."""

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest
from jsonschema import ValidationError
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from ship_invoice_agent.config import Settings
from ship_invoice_agent.contracts import (
    extraction_schema,
    parse_extraction,
    validate_contract,
)
from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.extraction.cited_fields import flatten_cited_payload
from ship_invoice_agent.extraction.evidence import build_catalog
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import RawDocument
from ship_invoice_agent.output import serialize_operations
from ship_invoice_agent.workflow import InvoiceAgent


@pytest.fixture
def bound_sample(wire_response):
    raw = RawDocument(
        source_path=Path("separate-title.docx"),
        source_name="separate-title.docx",
        source_type="docx",
        file_hash="bound-facts",
        content=(
            "Debit Note\nReference DN-100\nDate 2026-10-24\nVendor Ltd\nBuyer Ltd\n"
            "Container CX0007\nHandling Fee | 1 | 65.00 | USD | PP | 65.00 | CX0007\n"
            "Total USD 65.00"
        ),
    )
    payload = wire_response(
        raw,
        {
            "document_type": "Debit Note",
            "reference_no": "DN-100",
            "document_date": "2026-10-24",
            "issuer": "Vendor Ltd",
            "recipient": "Buyer Ltd",
            "container_numbers": ["CX0007"],
            "charges": [
                {
                    "original_charge_item": "Handling Fee",
                    "quantity": 1,
                    "unit_price": 65,
                    "amount": 65,
                    "currency": "USD",
                    "payment_term": "PP",
                    "container_numbers": ["CX0007"],
                }
            ],
            "total": 65,
        },
    )
    return raw, payload


def fact_at(payload, path):
    value = payload["documents"][0]
    for part in path.split("/"):
        value = value[int(part)] if isinstance(value, list) else value[part]
    return value


FACT_PATHS = [
    "document_type",
    "document_type_raw",
    "reference_no",
    "document_date",
    "container_numbers/0",
    "charges/0/amount",
    "charges/0/currency",
    "charges/0/container_numbers/0",
    "totals/0/total",
]


@pytest.mark.parametrize("path", FACT_PATHS)
@pytest.mark.parametrize("mutation", ["missing_refs", "empty_refs", "missing_value"])
def test_each_non_null_fact_requires_its_own_references(bound_sample, path, mutation):
    _, payload = bound_sample
    fact = fact_at(payload, path)
    if mutation == "missing_refs":
        del fact["source_line_ids"]
    elif mutation == "empty_refs":
        fact["source_line_ids"] = []
    else:
        del fact["value"]
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(payload))


@pytest.mark.parametrize("path", FACT_PATHS)
def test_each_fact_must_reference_the_current_input(bound_sample, path):
    raw, payload = bound_sample
    fact_at(payload, path)["source_line_ids"] = ["other-file:L000001"]
    with pytest.raises(ValidationError):
        validate_contract(payload, "extraction", line_ids=list(build_catalog(raw)))


@pytest.mark.parametrize("path", [p for p in FACT_PATHS if "container_numbers" not in p])
def test_unknown_fact_has_null_value_and_empty_references(bound_sample, path):
    _, payload = bound_sample
    fact = fact_at(payload, path)
    fact.update(value=None, source_line_ids=[])
    parse_extraction(json.dumps(payload))
    fact["source_line_ids"] = ["irrelevant:L000001"]
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(payload))


def test_same_line_can_support_multiple_facts_and_one_fact_can_use_multiple_lines(bound_sample):
    raw, payload = bound_sample
    line_ids = list(build_catalog(raw))
    fact_at(payload, "charges/0/currency")["source_line_ids"] = list(
        fact_at(payload, "charges/0/amount")["source_line_ids"]
    )
    fact_at(payload, "document_type")["source_line_ids"] += [line_ids[1]]
    validate_contract(payload, "extraction", line_ids=line_ids)


def test_missing_reference_fails_before_evidence_adapter(bound_sample, monkeypatch):
    raw, payload = bound_sample
    fact_at(payload, "document_type")["source_line_ids"] = []
    extractor = InvoiceExtractor(
        FakeListChatModel(responses=[json.dumps(payload)]),
        structured_output_mode="prompt",
        max_retries=0,
    )

    def must_not_reach_adapter(*args, **kwargs):
        pytest.fail("schema failure must precede A07")

    monkeypatch.setattr(
        "ship_invoice_agent.extraction.chain.adapt_documents", must_not_reach_adapter
    )
    from ship_invoice_agent.exceptions import ExtractionError

    with pytest.raises(ExtractionError):
        extractor.extract_documents(raw)


def test_schema_failure_can_retry_with_a_complete_response(bound_sample):
    raw, valid = bound_sample
    invalid = copy.deepcopy(valid)
    fact_at(invalid, "document_type_raw")["source_line_ids"] = []

    class Native:
        calls = 0

        def with_structured_output(self, schema, **kwargs):
            return self

        def invoke(self, messages):
            payload = invalid if self.calls == 0 else valid
            self.calls += 1
            return {"raw": AIMessage(content=json.dumps(payload)), "parsed": payload}

    model = Native()
    extracted = InvoiceExtractor(model, max_retries=1).extract_documents(raw)
    assert model.calls == 2
    assert extracted[0].review_reasons == []


def test_correct_separate_title_citations_allow_normal_processing(bound_sample, tmp_path):
    raw, payload = bound_sample

    class Router:
        def parse(self, path, **kwargs):
            return [raw]

    history = tmp_path / "bills.json"
    history.write_text("[]")
    settings = Settings(_env_file=None, existing_bills_path=history)
    extractor = InvoiceExtractor(
        FakeListChatModel(responses=[json.dumps(payload)]), structured_output_mode="prompt"
    )
    document = extractor.extract(raw)
    assert document.extraction_schema_version == "v2"
    assert document.review_reasons == []
    title = next(e for e in document.evidence if e["field_path"] == "/document_type")
    assert title["method"] == "model_reference"
    assert title["sources"][0]["text"] == "Debit Note"
    agent = InvoiceAgent(settings, Router(), extractor, JsonBillRepository(history))
    operations = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert [op.operation for op in operations] == ["CREATE"]
    public = json.loads(serialize_operations(operations), parse_float=Decimal)
    assert public[0]["charge_details"]["amount"] == 65
    assert "value" not in public[0]["charge_details"]
    assert JsonBillRepository(history).all()[0].audit_history[0].schema_version == "v2"


def test_bound_citation_does_not_bypass_content_verification(bound_sample):
    raw, payload = bound_sample
    issuer = fact_at(payload, "issuer")
    fact_at(payload, "document_type")["source_line_ids"] = list(issuer["source_line_ids"])
    validate_contract(payload, "extraction", line_ids=list(build_catalog(raw)))
    document = InvoiceExtractor(
        FakeListChatModel(responses=[json.dumps(payload)]), structured_output_mode="prompt"
    ).extract(raw)
    assert "UNSUPPORTED_FACT:/document_type" in document.review_reasons


def test_observation_policy_is_unchanged_by_binding(bound_sample):
    raw, payload = bound_sample
    payload["documents"][0]["observations"] = [
        {
            "code": "OCR_UNCERTAIN",
            "field_paths": ["/reference_no"],
            "source_line_ids": fact_at(payload, "reference_no")["source_line_ids"],
            "detail": "Possible OCR issue",
        }
    ]
    document = InvoiceExtractor(
        FakeListChatModel(responses=[json.dumps(payload)]), structured_output_mode="prompt"
    ).extract(raw)
    assert document.review_reasons == ["EXTRACTION_OBSERVATION:OCR_UNCERTAIN"]


def test_flattening_preserves_metadata_and_does_not_modify_wire_payload(bound_sample):
    _, payload = bound_sample
    before = copy.deepcopy(payload)
    flat = flatten_cited_payload(payload)
    assert payload == before
    assert flat["documents"][0]["charges"][0]["container_numbers"] == ["CX0007"]
    assert any(
        e["field_path"] == "/charges/0/container_numbers/0"
        for e in flat["documents"][0]["evidence"]
    )
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(flat))


@pytest.mark.parametrize("version", ["v1", "v2"])
def test_runtime_and_published_versions_stay_identical(version):
    assert extraction_schema(version) == json.loads(
        Path(f"schemas/draft/extraction-{version}.schema.json").read_text()
    )
