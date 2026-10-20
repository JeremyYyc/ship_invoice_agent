from dataclasses import dataclass, field
from typing import Any

from ship_invoice_agent.models import ExtractedCharge, ExtractedDocument, Operation
from ship_invoice_agent.models.bill import ExistingBill
from ship_invoice_agent.normalization import normalize_identifier, normalize_text


@dataclass
class MatchDecision:
    operation: Operation
    matched_bill: ExistingBill | None = None
    strategy: str | None = None
    changed_fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    reasoning: list[str] = field(default_factory=list)


class BillMatcher:
    def __init__(self, bills: list[ExistingBill]):
        self.bills = bills

    def decide(self, document: ExtractedDocument, charge: ExtractedCharge) -> MatchDecision:
        strategies = [
            ("REFERENCE_NO_CHARGE_ITEM", self._by_reference_charge),
            ("BILL_OF_LADING_CHARGE_CURRENCY", self._by_bl_charge_currency),
            ("BOOKING_CHARGE_CURRENCY", self._by_booking_charge_currency),
            ("CONTAINER_PARTIES_DATE_AMOUNT", self._by_container_party_date_amount),
        ]
        for strategy, matcher in strategies:
            candidates = [bill for bill in self.bills if matcher(bill, document, charge)]
            if not candidates:
                continue
            if len(candidates) > 1:
                exact = [bill for bill in candidates if self._same_material_values(bill, charge)]
                if len(exact) == 1:
                    return MatchDecision(
                        operation=Operation.NO_CHANGE,
                        matched_bill=exact[0],
                        strategy=strategy,
                        reasoning=["one exact record found among multiple identity candidates"],
                    )
                return MatchDecision(
                    operation=Operation.PENDING_REVIEW,
                    strategy=strategy,
                    conflicts=[
                        {
                            "code": "AMBIGUOUS_MATCH",
                            "candidate_bill_ids": [bill.bill_id for bill in candidates],
                        }
                    ],
                    reasoning=["multiple historical bills match the same charge identity"],
                )
            return self._compare(candidates[0], charge, strategy, document)
        return MatchDecision(
            operation=Operation.CREATE,
            reasoning=["no historical bill matched the configured strategies"],
        )

    @staticmethod
    def _charge_name(value: str) -> str:
        return normalize_text(value)

    def _by_reference_charge(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        return bool(
            document.reference_no
            and normalize_identifier(bill.document_info.reference_no)
            == normalize_identifier(document.reference_no)
            and self._charge_name(bill.charge_details.charge_item)
            == self._charge_name(charge.charge_item or charge.original_charge_item)
        )

    def _by_bl_charge_currency(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        current_numbers = {
            normalize_identifier(document.master_bill_of_lading_no),
            normalize_identifier(document.house_bill_of_lading_no),
        } - {""}
        historical_numbers = {
            normalize_identifier(bill.shipment_info.get("master_bill_of_lading_no")),
            normalize_identifier(bill.shipment_info.get("house_bill_of_lading_no")),
        } - {""}
        return bool(
            current_numbers & historical_numbers
            and self._charge_name(bill.charge_details.charge_item)
            == self._charge_name(charge.charge_item or charge.original_charge_item)
            and (bill.charge_details.currency or "") == (charge.currency or "")
        )

    def _by_booking_charge_currency(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        return bool(
            document.booking_no
            and normalize_identifier(bill.shipment_info.get("booking_no"))
            == normalize_identifier(document.booking_no)
            and self._charge_name(bill.charge_details.charge_item)
            == self._charge_name(charge.charge_item or charge.original_charge_item)
            and (bill.charge_details.currency or "") == (charge.currency or "")
        )

    def _by_container_party_date_amount(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        bill_containers = {
            normalize_identifier(value) for value in bill.shipment_info.get("container_numbers", [])
        }
        current = {normalize_identifier(value) for value in document.container_numbers}
        return bool(
            current & bill_containers
            and normalize_text(bill.transaction_party.issuer) == normalize_text(document.issuer)
            and normalize_text(bill.transaction_party.recipient)
            == normalize_text(document.recipient)
            and bill.document_info.document_date == document.document_date
            and bill.charge_details.amount == float(charge.amount)
        )

    @staticmethod
    def _same_material_values(bill: ExistingBill, charge: ExtractedCharge) -> bool:
        return (
            bill.charge_details.amount == float(charge.amount)
            and (bill.charge_details.currency or "") == (charge.currency or "")
            and (bill.charge_details.payment_term or "") == (charge.payment_term or "")
        )

    def _compare(
        self,
        bill: ExistingBill,
        charge: ExtractedCharge,
        strategy: str,
        document: ExtractedDocument,
    ) -> MatchDecision:
        conflicts: list[dict[str, Any]] = []
        if bill.charge_details.currency and charge.currency:
            if bill.charge_details.currency != charge.currency:
                conflicts.append(
                    {
                        "field": "currency",
                        "existing_value": bill.charge_details.currency,
                        "extracted_value": charge.currency,
                    }
                )
        if document.issuer and bill.transaction_party.issuer:
            if normalize_text(document.issuer) != normalize_text(bill.transaction_party.issuer):
                conflicts.append(
                    {
                        "field": "issuer",
                        "existing_value": bill.transaction_party.issuer,
                        "extracted_value": document.issuer,
                    }
                )
        if conflicts:
            return MatchDecision(
                operation=Operation.PENDING_REVIEW,
                matched_bill=bill,
                strategy=strategy,
                conflicts=conflicts,
                reasoning=["identity matched but strong fields conflict"],
            )
        changed: dict[str, dict[str, Any]] = {}
        for name, old, new in (
            ("amount", bill.charge_details.amount, float(charge.amount)),
            ("payment_term", bill.charge_details.payment_term, charge.payment_term),
        ):
            if new is not None and old != new:
                changed[name] = {"old": old, "new": new}
        if not changed:
            return MatchDecision(
                operation=Operation.NO_CHANGE,
                matched_bill=bill,
                strategy=strategy,
                reasoning=["historical bill and extracted charge have the same material values"],
            )
        return MatchDecision(
            operation=Operation.UPDATE,
            matched_bill=bill,
            strategy=strategy,
            changed_fields=changed,
            reasoning=["a unique historical bill matched and explicit fields changed"],
        )
