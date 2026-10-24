import hashlib
import json
import uuid
from pathlib import Path
from typing import Protocol

from ship_invoice_agent.accounting import classify_posting, resolve_accounting_entity
from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import ExtractionTimeoutError
from ship_invoice_agent.matching import BillMatcher, JsonBillRepository
from ship_invoice_agent.models import (
    AuditTrail,
    BillOperation,
    ChargeDetails,
    DocumentInfo,
    ExtractedCharge,
    ExtractedDocument,
    FinancialAttributes,
    Operation,
    RawDocument,
    TransactionParty,
)
from ship_invoice_agent.models.bill import ExistingBill
from ship_invoice_agent.normalization import NormalizationService, normalize_text
from ship_invoice_agent.parsers import DocumentRouter
from ship_invoice_agent.validation import validate_document


class Extractor(Protocol):
    def extract(self, document: RawDocument) -> ExtractedDocument: ...


class InvoiceAgent:
    def __init__(
        self,
        settings: Settings,
        router: DocumentRouter,
        extractor: Extractor,
        repository: JsonBillRepository,
    ):
        self.settings = settings
        self.router = router
        self.extractor = extractor
        self.repository = repository
        self.normalizer = NormalizationService(
            settings.charge_aliases_path, settings.party_aliases_path
        )
        self._seen_fingerprints: set[str] = set()

    def process(
        self,
        path: Path,
        *,
        file_url: str | None = None,
        accounting_entity: str | None = None,
        apply: bool | None = None,
    ) -> list[BillOperation]:
        accounting_entity = accounting_entity or self.settings.default_accounting_entity
        if accounting_entity:
            accounting_entity = resolve_accounting_entity(
                accounting_entity, self.settings.accounting_entities_path
            )
        should_apply = apply if apply is not None else self.settings.apply_bill_changes
        operations: list[BillOperation] = []
        for raw in self.router.parse(path, file_url=file_url):
            try:
                extracted = self.normalizer.normalize_document(self.extractor.extract(raw))
            except ExtractionTimeoutError:
                operations.append(
                    self._unresolved_operation(
                        raw,
                        ExtractedDocument(warnings=["LLM_EXTRACTION_FAILED"]),
                        accounting_entity,
                        ["model extraction exhausted its retry/time budget"],
                        ["LLM_EXTRACTION_FAILED", "LLM_TIME_BUDGET_EXHAUSTED"],
                    )
                )
                continue
            validation = validate_document(extracted, self.settings.currencies_path)
            posting_type, accounting_reasoning = classify_posting(extracted, accounting_entity)
            if not extracted.charges:
                operations.append(
                    self._unresolved_operation(
                        raw,
                        extracted,
                        accounting_entity,
                        accounting_reasoning,
                        validation.errors + validation.warnings,
                    )
                )
                continue
            matcher = BillMatcher(self.repository.all())
            for charge in extracted.charges:
                decision = matcher.decide(extracted, charge)
                warnings = list(extracted.warnings) + validation.warnings
                fingerprint = self._fingerprint(extracted, charge)
                if fingerprint in self._seen_fingerprints:
                    decision.operation = Operation.NO_CHANGE
                    decision.reasoning.append("duplicate charge already appeared in this batch")
                    warnings.append("DUPLICATE_IN_CURRENT_BATCH")
                else:
                    self._seen_fingerprints.add(fingerprint)
                if (
                    validation.requires_review
                    or charge.confidence < self.settings.min_auto_accept_confidence
                    or posting_type is None
                ):
                    decision.operation = Operation.PENDING_REVIEW
                    warnings.extend(validation.errors)
                    if charge.confidence < self.settings.min_auto_accept_confidence:
                        warnings.append("LOW_EXTRACTION_CONFIDENCE")
                    if posting_type is None:
                        warnings.append("AR_AP_UNRESOLVED")
                operation = BillOperation(
                    operation=decision.operation,
                    document_info=DocumentInfo(
                        document_type=extracted.document_type,
                        reference_no=extracted.reference_no,
                        document_date=extracted.document_date,
                    ),
                    transaction_party=TransactionParty(
                        issuer=extracted.issuer,
                        recipient=extracted.recipient,
                    ),
                    charge_details=ChargeDetails(
                        charge_item=charge.charge_item or charge.original_charge_item,
                        amount=float(charge.amount),
                        currency=charge.currency,
                        payment_term=charge.payment_term,
                    ),
                    financial_attributes=FinancialAttributes(
                        accounting_entity=accounting_entity,
                        posting_type=posting_type,
                    ),
                    audit_trail=AuditTrail(
                        source_file=raw.source_name,
                        source_page=charge.source_page,
                        original_charge_item=charge.original_charge_item,
                        source_text=charge.source_text,
                        file_hash=raw.file_hash,
                        matched_bill_id=(
                            decision.matched_bill.bill_id if decision.matched_bill else None
                        ),
                        match_strategy=decision.strategy,
                        changed_fields=decision.changed_fields,
                        conflicts=decision.conflicts,
                        confidence=charge.confidence,
                        ai_reasoning=decision.reasoning + accounting_reasoning,
                        warnings=sorted(set(warnings)),
                    ),
                )
                operations.append(operation)
                if should_apply:
                    self._apply(operation, extracted)
        if should_apply:
            self.repository.save()
        return operations

    @staticmethod
    def _fingerprint(document: ExtractedDocument, charge: ExtractedCharge) -> str:
        payload = {
            "reference": document.reference_no,
            "mbl": document.master_bill_of_lading_no,
            "hbl": document.house_bill_of_lading_no,
            "issuer": normalize_text(document.issuer),
            "recipient": normalize_text(document.recipient),
            "charge": normalize_text(charge.charge_item or charge.original_charge_item),
            "amount": str(charge.amount),
            "currency": charge.currency,
        }
        return hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()

    @staticmethod
    def _unresolved_operation(
        raw: RawDocument,
        document: ExtractedDocument,
        accounting_entity: str | None,
        reasoning: list[str],
        warnings: list[str],
    ) -> BillOperation:
        return BillOperation(
            operation=Operation.PENDING_REVIEW,
            document_info=DocumentInfo(
                document_type=document.document_type,
                reference_no=document.reference_no,
                document_date=document.document_date,
            ),
            transaction_party=TransactionParty(
                issuer=document.issuer,
                recipient=document.recipient,
            ),
            charge_details=ChargeDetails(
                charge_item=None,
                amount=None,
                currency=None,
                payment_term=None,
            ),
            financial_attributes=FinancialAttributes(
                accounting_entity=accounting_entity,
                posting_type=None,
            ),
            audit_trail=AuditTrail(
                source_file=raw.source_name,
                file_hash=raw.file_hash,
                confidence=0,
                ai_reasoning=reasoning,
                warnings=sorted(set(warnings + ["NO_VALID_CHARGE_RECORD"])),
            ),
        )

    def _apply(self, operation: BillOperation, document: ExtractedDocument) -> None:
        if operation.operation not in {Operation.CREATE, Operation.UPDATE}:
            return
        bill_id = operation.audit_trail.matched_bill_id or f"BILL-{uuid.uuid4().hex[:12].upper()}"
        existing = None
        if operation.operation == Operation.UPDATE:
            existing = next(
                (item for item in self.repository.all() if item.bill_id == bill_id), None
            )
        if existing:
            data = existing.model_dump()
            for field, values in operation.audit_trail.changed_fields.items():
                data["charge_details"][field] = values["new"]
            bill = ExistingBill.model_validate(data)
        else:
            bill = ExistingBill(
                bill_id=bill_id,
                document_info=operation.document_info,
                transaction_party=operation.transaction_party,
                charge_details=operation.charge_details,
                financial_attributes=operation.financial_attributes,
                shipment_info={
                    "master_bill_of_lading_no": document.master_bill_of_lading_no,
                    "house_bill_of_lading_no": document.house_bill_of_lading_no,
                    "booking_no": document.booking_no,
                    "container_numbers": document.container_numbers,
                },
            )
        self.repository.upsert(bill)
