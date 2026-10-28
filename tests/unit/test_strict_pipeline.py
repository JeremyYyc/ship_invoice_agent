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
    request_extraction_schema,
    validate_contract,
)
from ship_invoice_agent.exceptions import (
    ConfigurationError,
    ExtractionError,
    RepositoryConflictError,
)
from ship_invoice_agent.extraction import InvoiceExtractor
from ship_invoice_agent.extraction.cited_fields import flatten_cited_payload
from ship_invoice_agent.extraction.evidence import build_catalog
from ship_invoice_agent.matching import JsonBillRepository
from ship_invoice_agent.models import RawDocument
from ship_invoice_agent.models.bill import ExistingBill
from ship_invoice_agent.output import serialize_operations
from ship_invoice_agent.workflow import InvoiceAgent


@pytest.fixture
def sample(wire_response):
    raw = RawDocument(
        source_path=Path("test.pdf"),
        source_name="test.pdf",
        source_type="pdf",
        file_hash="schema-test",
        content=(
            "[PAGE 1]\nInvoice INV-001\nVendor Ltd\nBuyer Ltd\n"
            "Handling Fee | 1 | 65.00 | USD | PP | 65.00\nTotal USD 65.00"
        ),
    )
    payload = wire_response(
        raw,
        {
            "document_type": "Invoice",
            "reference_no": "INV-001",
            "issuer": "Vendor Ltd",
            "recipient": "Buyer Ltd",
            "charges": [
                {
                    "original_charge_item": "Handling Fee",
                    "quantity": 1,
                    "unit_price": 65,
                    "amount": 65,
                    "currency": "USD",
                    "payment_term": "PP",
                }
            ],
            "total": 65,
        },
    )
    return raw, payload


def make_agent(tmp_path, raw, payload):
    path = tmp_path / "bills.json"
    path.write_text("[]")
    settings = Settings(_env_file=None, existing_bills_path=path)

    class Router:
        def parse(self, *args, **kwargs):
            return [raw]

    extractor = InvoiceExtractor(
        FakeListChatModel(responses=[json.dumps(payload)]),
        structured_output_mode="prompt",
        max_retries=0,
    )
    return InvoiceAgent(settings, Router(), extractor, JsonBillRepository(path))


def test_native_request_explicitly_enforces_schema(sample):
    raw, payload = sample

    class Native:
        def with_structured_output(self, schema, **kwargs):
            self.schema, self.kwargs = schema, kwargs
            return self

        def invoke(self, messages):
            assert ":L000" in messages[-1].content
            return {
                "raw": AIMessage(content=json.dumps(payload)),
                "parsed": payload,
                "parsing_error": None,
            }

    model = Native()
    documents = InvoiceExtractor(model, structured_output_mode="native").extract_documents(raw)
    assert model.kwargs == {"method": "json_schema", "strict": True, "include_raw": True}
    assert model.schema["additionalProperties"] is False
    assert documents[0].review_reasons == []
    assert documents[0].charges[0].confidence is None
    assert documents[0].charges[0].amount == Decimal("65")


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "string_amount", "boolean_amount", "numeric_id"]
)
def test_strict_schema_rejects_wrong_shape_and_types(sample, mutation):
    _, payload = sample
    document = payload["documents"][0]
    if mutation == "extra":
        document["charges"][0]["confidence"] = 0.99
    elif mutation == "missing":
        del document["booking_no"]
    elif mutation == "string_amount":
        document["charges"][0]["amount"]["value"] = "65.0"
    elif mutation == "boolean_amount":
        document["charges"][0]["amount"]["value"] = True
    else:
        document["reference_no"]["value"] = 1001
    with pytest.raises(ValidationError):
        parse_extraction(json.dumps(payload))


def test_decimal_lexeme_is_not_rounded_through_float(sample):
    _, payload = sample
    payload["documents"][0]["charges"][0]["amount"]["value"] = "PRECISE"
    content = json.dumps(payload).replace('"PRECISE"', "0.1234567890123456789")
    assert parse_extraction(content)["documents"][0]["charges"][0]["amount"]["value"] == Decimal(
        "0.1234567890123456789"
    )


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity"])
def test_non_finite_wire_numbers_are_rejected(sample, value):
    _, payload = sample
    payload["documents"][0]["charges"][0]["amount"]["value"] = "NON_FINITE"
    with pytest.raises(ValueError):
        parse_extraction(json.dumps(payload).replace('"NON_FINITE"', value))


@pytest.mark.parametrize("finish", ["length", "content_filter"])
def test_incomplete_native_response_is_not_accepted_or_retried(sample, finish):
    raw, payload = sample

    class Native:
        calls = 0

        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            self.calls += 1
            return {
                "raw": AIMessage(
                    content=json.dumps(payload), response_metadata={"finish_reason": finish}
                ),
                "parsed": payload,
                "parsing_error": None,
            }

    model = Native()
    with pytest.raises(ExtractionError):
        InvoiceExtractor(model, structured_output_mode="native").extract(raw)
    assert model.calls == 1


def test_unsupported_adapter_fails_without_silent_fallback():
    with pytest.raises(ConfigurationError):
        InvoiceExtractor(object(), structured_output_mode="native")


@pytest.mark.parametrize("mutation", ["invented_line", "changed_identifier", "missing_evidence"])
def test_unsupported_facts_cannot_be_applied(tmp_path, sample, mutation):
    raw, payload = sample
    document = payload["documents"][0]
    if mutation == "invented_line":
        document["document_type"]["source_line_ids"] = ["invented"]
    elif mutation == "changed_identifier":
        document["reference_no"]["value"] = "INV-999"
    else:
        document["document_type"]["source_line_ids"] = []
    agent = make_agent(tmp_path, raw, payload)
    operations = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert operations[0].operation == "PENDING_REVIEW"
    assert agent.repository.path.read_text() == "[]"
    assert agent.repository.all() == []


def test_schema_failure_becomes_pending_review(tmp_path, sample):
    raw, payload = sample
    del payload["documents"][0]["charges"]
    agent = make_agent(tmp_path, raw, payload)
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert result[0].operation == "PENDING_REVIEW"
    assert "LLM_SCHEMA_VALIDATION_FAILED" in result[0].audit_trail.warnings
    assert agent.repository.path.read_text() == "[]"


def test_preview_does_not_modify_repository_or_leak_batch_state(tmp_path, sample):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    for _ in range(2):
        result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=False)
        assert result[0].operation == "CREATE"
        assert agent.repository.all() == []
        assert agent.repository.path.read_text() == "[]"


def test_final_validation_failure_prevents_any_write(tmp_path, sample, monkeypatch):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)

    def invalid_output(*args, **kwargs):
        raise ValueError("final JSON rejected")

    monkeypatch.setattr("ship_invoice_agent.workflow.agent.serialize_operations", invalid_output)
    with pytest.raises(ValueError, match="final JSON"):
        agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert agent.repository.path.read_text() == "[]"
    assert agent.repository.all() == []


def test_failed_persistence_does_not_mutate_live_repository(tmp_path, sample, monkeypatch):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)

    def failed_save(self):
        raise OSError("disk full")

    monkeypatch.setattr(JsonBillRepository, "save", failed_save)
    with pytest.raises(OSError, match="disk full"):
        agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert agent.repository.path.read_text() == "[]"
    assert agent.repository.all() == []


def test_apply_then_reload_is_idempotent(tmp_path, sample):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert result[0].operation == "CREATE"
    stored = JsonBillRepository(agent.repository.path).all()[0]
    assert stored.audit_history[0].schema_version == "v2"
    assert stored.audit_history[0].evidence
    assert stored.audit_history[0].processed_at
    agent.repository = JsonBillRepository(agent.repository.path)
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert result[0].operation == "NO_CHANGE"
    assert len(agent.repository.all()) == 1


def test_historical_version_change_aborts_commit(tmp_path, sample):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    agent.repository.path.write_text("[ ]")
    with pytest.raises(RepositoryConflictError):
        agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert agent.repository.path.read_text() == "[ ]"
    assert not agent.repository.path.with_suffix(".json.lock").exists()
    assert agent.repository.all() == []


def test_multi_document_batch_keeps_independent_invoices(tmp_path, sample):
    raw, payload = sample
    raw.content += "\nInvoice INV-002"
    other = copy.deepcopy(payload["documents"][0])
    other["reference_no"]["value"] = "INV-002"
    line_id = next(key for key, line in build_catalog(raw).items() if "INV-002" in line["text"])
    other["reference_no"]["source_line_ids"] = [line_id]
    payload["documents"].append(other)
    agent = make_agent(tmp_path, raw, payload)
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert [op.operation for op in result] == ["CREATE", "CREATE"]
    assert len(agent.repository.all()) == 2


def test_partial_payment_update_inherits_all_other_fields(tmp_path, wire_response):
    raw = RawDocument(
        source_path=Path("change.eml"),
        source_name="change.eml",
        source_type="eml",
        file_hash="change",
        content=("Invoice DC108478\nHandling Charge: change payment term to CC"),
    )
    payload = wire_response(
        raw,
        {
            "document_type": "Invoice",
            "reference_no": "DC108478",
            "charges": [
                {"original_charge_item": "Handling Charge", "amount": None, "payment_term": "CC"}
            ],
        },
    )
    ids = [key for key, line in build_catalog(raw).items() if "change payment" in line["text"]]
    payload["documents"][0]["change_requests"] = [
        {
            "field_path": "/charges/0/payment_term",
            "action": "SET",
            "new_value_text": "CC",
            "source_line_ids": ids,
        }
    ]
    agent = make_agent(tmp_path, raw, payload)
    existing = json.loads(Path("tests/fixtures/existing_bills/amount_update.json").read_text())[0]
    existing["charge_details"]["payment_term"] = "PP"
    agent.repository.upsert(ExistingBill.model_validate(existing))
    agent.repository.save()
    result = agent.process(
        raw.source_path, accounting_entity="Pilot Aiship Tech co., Ltd.", apply=True
    )
    assert result[0].operation == "UPDATE"
    assert result[0].charge_details.amount == 60
    assert result[0].charge_details.currency == "USD"
    assert result[0].charge_details.payment_term == "CC"
    assert result[0].document_info.document_date == "2026-10-24"
    assert result[0].transaction_party.recipient == "AEBO TCO IMPORT LLC"
    persisted = JsonBillRepository(agent.repository.path).all()[0]
    assert persisted.charge_details == result[0].charge_details


def test_runtime_contracts_match_published_schema_files():
    assert extraction_schema() == json.loads(
        Path("schemas/draft/extraction-v2.schema.json").read_text()
    )
    validate_contract([], "assignment-output")


def test_request_schema_constrains_line_ids_for_bound_facts(sample):
    raw, payload = sample
    schema = request_extraction_schema(list(build_catalog(raw)))
    assert set(schema["$defs"]["source_line_id"]["enum"]) == set(build_catalog(raw))
    assert "evidence" not in schema["$defs"]["document"]["properties"]
    from jsonschema import Draft202012Validator

    Draft202012Validator.check_schema(schema)
    validator = Draft202012Validator(schema)
    validator.validate(payload)
    payload["documents"][0]["document_type"]["source_line_ids"] = ["invented"]
    with pytest.raises(ValidationError):
        validator.validate(payload)


def test_same_source_fee_row_cannot_be_created_twice(tmp_path, sample):
    raw, payload = sample
    document = payload["documents"][0]
    document["charges"].append(copy.deepcopy(document["charges"][0]))
    agent = make_agent(tmp_path, raw, payload)
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert all(op.operation == "PENDING_REVIEW" for op in result)
    assert agent.repository.path.read_text() == "[]"


def test_snapshot_difference_without_change_instruction_requires_review(tmp_path, sample):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    existing = ExistingBill.model_validate(
        {
            "bill_id": "OLD",
            "document_info": {
                "document_type": "Invoice",
                "reference_no": "INV-001",
                "document_date": None,
            },
            "transaction_party": {"issuer": "Vendor Ltd", "recipient": "Buyer Ltd"},
            "charge_details": {
                "charge_item": "Handling Charge",
                "amount": 60,
                "currency": "USD",
                "payment_term": "PP",
            },
            "shipment_info": {},
        }
    )
    agent.repository.upsert(existing)
    agent.repository.save()
    before = agent.repository.path.read_bytes()
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert result[0].operation == "PENDING_REVIEW"
    assert result[0].audit_trail.conflicts[0]["code"] == "UPDATE_INTENT_UNCONFIRMED"
    assert agent.repository.path.read_bytes() == before


def test_recipient_conflict_is_checked_even_with_matching_amount(tmp_path, sample):
    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    agent.repository.bills[0].transaction_party.recipient = "Different Buyer"
    agent.repository.save()
    result = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=True)
    assert result[0].operation == "PENDING_REVIEW"
    assert result[0].audit_trail.conflicts[0]["field"] == "recipient"


def test_currency_totals_are_checked_independently_and_include_tax():
    from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument
    from ship_invoice_agent.validation import validate_document

    document = ExtractedDocument(
        charges=[
            ExtractedCharge(original_charge_item="A", amount=Decimal("65"), currency="USD"),
            ExtractedCharge(original_charge_item="B", amount=Decimal("20"), currency="EUR"),
        ],
        totals=[
            {
                "currency": "USD",
                "subtotal": Decimal("65"),
                "tax": Decimal("5"),
                "discount": None,
                "total": Decimal("70"),
            },
            {
                "currency": "EUR",
                "subtotal": Decimal("20"),
                "tax": None,
                "discount": None,
                "total": Decimal("20"),
            },
        ],
    )
    assert not validate_document(document, Path("data/currencies.json")).errors
    document.totals[1]["total"] = Decimal("90")
    assert validate_document(document, Path("data/currencies.json")).requires_review


def test_type_evidence_can_only_be_derived_from_verified_reference_header(sample):
    from ship_invoice_agent.extraction.evidence import adapt_documents

    raw, payload = sample
    payload = flatten_cited_payload(payload)
    document = payload["documents"][0]
    document["evidence"] = [e for e in document["evidence"] if e["field_path"] != "/document_type"]
    extracted = adapt_documents(payload, build_catalog(raw))[0]
    assert extracted.review_reasons == []
    assert (
        next(e for e in extracted.evidence if e["field_path"] == "/document_type")["method"]
        == "verified_reference_header"
    )
    document["reference_no"] = "INVENTED"
    extracted = adapt_documents(payload, build_catalog(raw))[0]
    assert "UNSUPPORTED_FACT:/document_type" in extracted.review_reasons


def test_type_evidence_is_not_filled_from_unrelated_lines(sample):
    from ship_invoice_agent.extraction.evidence import adapt_documents

    raw, payload = sample
    payload = flatten_cited_payload(payload)
    document = payload["documents"][0]
    document["evidence"] = [e for e in document["evidence"] if e["field_path"] != "/document_type"]
    raw.content = raw.content.replace("Invoice INV-001", "Reference INV-001") + "\nInvoice OTHER"
    extracted = adapt_documents(payload, build_catalog(raw))[0]
    assert "UNSUPPORTED_FACT:/document_type" in extracted.review_reasons


def test_decimal_amount_survives_public_json_storage_and_matching(tmp_path, sample):
    from ship_invoice_agent.matching import BillMatcher
    from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument

    raw, payload = sample
    agent = make_agent(tmp_path, raw, payload)
    operation = agent.process(raw.source_path, accounting_entity="Vendor Ltd", apply=False)[0]
    exact = Decimal("9007199254740993.123456789")
    operation.charge_details.amount = exact
    output = json.loads(serialize_operations([operation]), parse_float=Decimal)
    assert output[0]["charge_details"]["amount"] == exact
    assert not isinstance(output[0]["charge_details"]["amount"], str)
    bill = ExistingBill(
        bill_id="PRECISE",
        document_info=operation.document_info,
        transaction_party=operation.transaction_party,
        charge_details=operation.charge_details,
    )
    agent.repository.upsert(bill)
    agent.repository.save()
    stored = JsonBillRepository(agent.repository.path).all()[0]
    assert stored.charge_details.amount == exact
    document = ExtractedDocument(
        reference_no=operation.document_info.reference_no,
        issuer="Vendor Ltd",
        recipient="Buyer Ltd",
        charges=[
            ExtractedCharge(
                original_charge_item="Handling Charge",
                amount=exact,
                currency="USD",
                payment_term="PP",
            )
        ],
    )
    assert BillMatcher([stored]).decide(document, document.charges[0]).operation == "NO_CHANGE"


def test_schema_rejection_is_configuration_error_without_retry(sample):
    raw, _ = sample

    class Native:
        calls = 0

        def with_structured_output(self, *args, **kwargs):
            return self

        def invoke(self, messages):
            self.calls += 1
            error = RuntimeError("unsupported response format")
            error.status_code = 400
            raise error

    model = Native()
    with pytest.raises(ConfigurationError):
        InvoiceExtractor(model, structured_output_mode="native").extract_documents(raw)
    assert model.calls == 1
