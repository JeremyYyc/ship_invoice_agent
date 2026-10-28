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

    def decide(
        self,
        document: ExtractedDocument,
        charge: ExtractedCharge,
        *,
        original_document: ExtractedDocument | None = None,
    ) -> MatchDecision:
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
                    decision = self._compare(exact[0], charge, strategy, document)
                    decision.reasoning.append(
                        "one exact record found among multiple identity candidates"
                    )
                    return decision
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
        conflicts = self._identifier_confusion_candidates(
            document, charge, original_document or document
        )
        if conflicts:
            return MatchDecision(
                operation=Operation.PENDING_REVIEW,
                strategy="OCR_CONFUSABLE_IDENTIFIER",
                conflicts=conflicts,
                reasoning=["possible OCR identifier confusion requires manual confirmation"],
            )
        return MatchDecision(
            operation=Operation.CREATE,
            reasoning=["no historical bill matched the configured strategies"],
        )

    def _identifier_confusion_candidates(
        self,
        document: ExtractedDocument,
        charge: ExtractedCharge,
        original_document: ExtractedDocument,
    ) -> list[dict[str, Any]]:
        """Find possible identities without correcting or accepting an OCR guess."""
        fields = (
            "reference_no",
            "master_bill_of_lading_no",
            "house_bill_of_lading_no",
            "booking_no",
            "container_numbers",
        )
        translation = str.maketrans({"O": "0", "I": "1"})
        conflicts = []
        for bill in self.bills:
            if self._charge_name(bill.charge_details.charge_item) != self._charge_name(
                charge.charge_item or charge.original_charge_item or ""
            ):
                continue
            for field_name in fields:
                current = getattr(document, field_name)
                original = getattr(original_document, field_name)
                if not current:
                    continue
                if field_name == "reference_no":
                    historical = [(field_name, bill.document_info.reference_no)]
                elif field_name in {"master_bill_of_lading_no", "house_bill_of_lading_no"}:
                    historical = [
                        (name, bill.shipment_info.get(name))
                        for name in ("master_bill_of_lading_no", "house_bill_of_lading_no")
                    ]
                else:
                    historical = [(field_name, bill.shipment_info.get(field_name))]
                # Use the unnormalized values so the review retains exact extraction text.
                observed_values = original if isinstance(original, list) else [original]
                for observed in observed_values:
                    normalized = normalize_identifier(observed)
                    if not normalized:
                        continue
                    for historical_field, value in historical:
                        candidate_values = value if isinstance(value, list) else [value]
                        for candidate in candidate_values:
                            candidate_normalized = normalize_identifier(candidate)
                            if (
                                candidate_normalized
                                and normalized != candidate_normalized
                                and normalized.translate(translation)
                                == candidate_normalized.translate(translation)
                            ):
                                conflicts.append(
                                    {
                                        "code": "POSSIBLE_OCR_IDENTIFIER_CONFUSION",
                                        "field": field_name,
                                        "extracted_value": observed,
                                        "normalized_value": normalized,
                                        "candidate_bill_id": bill.bill_id,
                                        "candidate_field": historical_field,
                                        "candidate_value": candidate,
                                    }
                                )
        return conflicts

    @staticmethod
    def _charge_name(value: str) -> str:
        return normalize_text(value)

    def _by_reference_charge(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        return bool(
            normalize_identifier(document.reference_no)
            and normalize_identifier(bill.document_info.reference_no)
            == normalize_identifier(document.reference_no)
            and self._charge_name(bill.charge_details.charge_item)
            == self._charge_name(charge.charge_item or charge.original_charge_item or "")
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
            == self._charge_name(charge.charge_item or charge.original_charge_item or "")
            and (bill.charge_details.currency or "") == (charge.currency or "")
        )

    def _by_booking_charge_currency(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        return bool(
            normalize_identifier(document.booking_no)
            and normalize_identifier(bill.shipment_info.get("booking_no"))
            == normalize_identifier(document.booking_no)
            and self._charge_name(bill.charge_details.charge_item)
            == self._charge_name(charge.charge_item or charge.original_charge_item or "")
            and (bill.charge_details.currency or "") == (charge.currency or "")
        )

    def _by_container_party_date_amount(
        self, bill: ExistingBill, document: ExtractedDocument, charge: ExtractedCharge
    ) -> bool:
        bill_containers = {
            normalize_identifier(value) for value in bill.shipment_info.get("container_numbers", [])
        } - {""}
        current = {normalize_identifier(value) for value in document.container_numbers} - {""}
        return bool(
            charge.amount is not None
            and current & bill_containers
            and normalize_text(bill.transaction_party.issuer) == normalize_text(document.issuer)
            and normalize_text(bill.transaction_party.recipient)
            == normalize_text(document.recipient)
            and bill.document_info.document_date == document.document_date
            and bill.charge_details.amount == charge.amount
        )

    @staticmethod
    def _same_material_values(bill: ExistingBill, charge: ExtractedCharge) -> bool:
        return (
            (charge.amount is None or bill.charge_details.amount == charge.amount)
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
        for party_field in ("issuer", "recipient"):
            observed = getattr(document, party_field)
            existing = getattr(bill.transaction_party, party_field)
            if observed and existing and normalize_text(observed) != normalize_text(existing):
                conflicts.append(
                    {
                        "field": party_field,
                        "existing_value": existing,
                        "extracted_value": observed,
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
            (
                "amount",
                bill.charge_details.amount,
                charge.amount,
            ),
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
        if document.extraction_schema_version and not set(changed).issubset(
            charge.explicit_update_fields
        ):
            return MatchDecision(
                operation=Operation.PENDING_REVIEW,
                matched_bill=bill,
                strategy=strategy,
                conflicts=[{"code": "UPDATE_INTENT_UNCONFIRMED"}],
                reasoning=["material differences lack a verified explicit change instruction"],
            )
        return MatchDecision(
            operation=Operation.UPDATE,
            matched_bill=bill,
            strategy=strategy,
            changed_fields=changed,
            reasoning=["a unique historical bill matched and explicit fields changed"],
        )
