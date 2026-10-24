import json
from dataclasses import dataclass
from pathlib import Path

from ship_invoice_agent.models import BillOperation
from ship_invoice_agent.output import AssignmentBillOperation


@dataclass
class EvaluationMetrics:
    cases: int = 0
    expected_charges: int = 0
    matched_charges: int = 0
    correct_amounts: int = 0
    correct_currencies: int = 0
    correct_references: int = 0
    invalid_json: int = 0

    def as_dict(self) -> dict[str, float | int]:
        charge_denominator = max(self.expected_charges, 1)
        case_denominator = max(self.cases, 1)
        return {
            "cases": self.cases,
            "expected_charges": self.expected_charges,
            "charge_recall": self.matched_charges / charge_denominator,
            "amount_accuracy": self.correct_amounts / charge_denominator,
            "currency_accuracy": self.correct_currencies / charge_denominator,
            "reference_accuracy": self.correct_references / case_denominator,
            "invalid_json": self.invalid_json,
        }


def evaluate_output(actual_path: Path, golden_path: Path) -> EvaluationMetrics:
    metrics = EvaluationMetrics(cases=1)
    try:
        actual_raw = json.loads(actual_path.read_text(encoding="utf-8"))
        actual = []
        for item in actual_raw:
            try:
                actual.append(AssignmentBillOperation.model_validate(item))
            except Exception:
                actual.append(BillOperation.model_validate(item))
    except Exception:
        metrics.invalid_json = 1
        return metrics
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    expected_charges = golden.get("charges", [])
    metrics.expected_charges = len(expected_charges)
    if golden.get("reference_no") and any(
        item.document_info.reference_no == golden["reference_no"] for item in actual
    ):
        metrics.correct_references = 1
    unmatched = list(actual)
    for expected in expected_charges:
        candidates = [
            item for item in unmatched if item.charge_details.charge_item == expected["charge_item"]
        ]
        if not candidates:
            continue
        candidate = min(
            candidates,
            key=lambda item: abs((item.charge_details.amount or 0) - expected["amount"]),
        )
        unmatched.remove(candidate)
        metrics.matched_charges += 1
        if candidate.charge_details.amount == expected["amount"]:
            metrics.correct_amounts += 1
        if candidate.charge_details.currency == expected["currency"]:
            metrics.correct_currencies += 1
    return metrics
