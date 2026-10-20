import json
from pathlib import Path

from ship_invoice_agent.evaluation import evaluate_output


def test_evaluation_scores_exact_output(tmp_path: Path):
    actual = [
        {
            "operation": "CREATE",
            "document_info": {
                "document_type": "Debit Note",
                "reference_no": "DC108478",
                "document_date": "2026-10-24",
            },
            "transaction_party": {"issuer": "A", "recipient": "B"},
            "charge_details": {
                "charge_item": "Handling Charge",
                "amount": 65.0,
                "currency": "USD",
                "payment_term": None,
            },
            "financial_attributes": {"accounting_entity": "A", "posting_type": "AR"},
            "audit_trail": {"source_file": "x.pdf", "confidence": 1},
        }
    ]
    golden = {
        "reference_no": "DC108478",
        "charges": [{"charge_item": "Handling Charge", "amount": 65.0, "currency": "USD"}],
    }
    actual_path = tmp_path / "actual.json"
    golden_path = tmp_path / "golden.json"
    actual_path.write_text(json.dumps(actual), encoding="utf-8")
    golden_path.write_text(json.dumps(golden), encoding="utf-8")

    result = evaluate_output(actual_path, golden_path).as_dict()

    assert result["charge_recall"] == 1
    assert result["amount_accuracy"] == 1
    assert result["reference_accuracy"] == 1
