import json

from pydantic import ValidationError

from ship_invoice_agent.models import (
    AuditTrail,
    BillOperation,
    ChargeDetails,
    DocumentInfo,
    FinancialAttributes,
    Operation,
    PostingType,
    TransactionParty,
)
from ship_invoice_agent.output import AssignmentBillOperation, serialize_operations


def build_operation() -> BillOperation:
    return BillOperation(
        operation=Operation.CREATE,
        document_info=DocumentInfo(
            document_type="Invoice", reference_no="INV1", document_date=None
        ),
        transaction_party=TransactionParty(issuer="A", recipient="B"),
        charge_details=ChargeDetails(
            charge_item="Handling Charge",
            amount=65.0,
            currency="USD",
            payment_term="PP",
        ),
        financial_attributes=FinancialAttributes(
            accounting_entity="A", posting_type=PostingType.AR
        ),
        audit_trail=AuditTrail(
            source_file="invoice.pdf",
            confidence=1,
            ai_reasoning=[
                "no historical bill matched the configured strategies",
                "original_charge_item alias mapped deterministically to Handling Charge",
            ],
            changed_fields={"amount": {"old": 50.0, "new": 65.0}},
            conflicts=[
                {
                    "field": "currency",
                    "existing_value": "CNY",
                    "extracted_value": "USD",
                }
            ],
            warnings=[
                "MODEL_CHARGE_NAME_CONFLICT",
                "the OCR amount may require verification",
            ],
        ),
    )


def test_assignment_output_has_exact_standard_shape():
    parsed = json.loads(serialize_operations([build_operation()]))

    assert isinstance(parsed, list)
    assert set(parsed[0]) == {
        "operation",
        "document_info",
        "transaction_party",
        "charge_details",
        "financial_attributes",
        "audit_trail",
    }
    assert set(parsed[0]["document_info"]) == {
        "document_type",
        "reference_no",
        "document_date",
    }
    assert set(parsed[0]["transaction_party"]) == {"issuer", "recipient"}
    assert set(parsed[0]["charge_details"]) == {
        "charge_item",
        "amount",
        "currency",
        "payment_term",
    }
    assert set(parsed[0]["financial_attributes"]) == {
        "accounting_entity",
        "posting_type",
    }
    assert set(parsed[0]["audit_trail"]) == {"ai_reasoning"}
    assert isinstance(parsed[0]["charge_details"]["amount"], float)
    assert "source_file" not in parsed[0]["audit_trail"]
    reasoning = parsed[0]["audit_trail"]["ai_reasoning"]
    assert "未找到符合已配置匹配策略的历史账单" in reasoning
    assert "根据原始费用名称中的别名，确定性映射为Handling Charge" in reasoning
    assert "字段“金额”由 50.0 更新为 65.0" in reasoning
    assert "字段“币种”存在冲突：历史值为 CNY，提取值为 USD" in reasoning
    assert "模型费用名称与原始证据的确定性映射冲突" in reasoning
    assert "模型提示该文档存在字段解释或 OCR 识别不确定性" in reasoning
    assert "the OCR amount may require verification" not in reasoning


def test_internal_output_mode_preserves_diagnostics():
    parsed = json.loads(serialize_operations([build_operation()], schema_mode="internal"))

    assert parsed[0]["audit_trail"]["source_file"] == "invoice.pdf"
    assert parsed[0]["audit_trail"]["ai_reasoning"] == [
        "no historical bill matched the configured strategies",
        "original_charge_item alias mapped deterministically to Handling Charge",
    ]
    assert parsed[0]["audit_trail"]["warnings"] == [
        "MODEL_CHARGE_NAME_CONFLICT",
        "the OCR amount may require verification",
    ]


def test_assignment_schema_rejects_additional_fields():
    payload = json.loads(serialize_operations([build_operation()]))[0]
    payload["audit_trail"]["source_file"] = "invoice.pdf"

    try:
        AssignmentBillOperation.model_validate(payload)
    except ValidationError:
        pass
    else:
        raise AssertionError("assignment schema accepted a non-standard audit field")


def test_amount_is_json_number():
    parsed = json.loads(serialize_operations([build_operation()]))
    assert isinstance(parsed[0]["charge_details"]["amount"], float)
