import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from ship_invoice_agent.accounting import classify_posting, resolve_accounting_entity
from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import ExtractionError, ExtractionTimeoutError
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
from ship_invoice_agent.normalization import (
    NormalizationService,
    normalize_identifier,
    normalize_text,
)
from ship_invoice_agent.output import serialize_operations
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
            settings.charge_aliases_path,
            settings.party_aliases_path,
            settings.charge_alias_conflict_policy,
        )

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
        staged = self.repository.fork()
        seen: dict[str, tuple[int, Operation]] = {}
        document_index = 0
        for raw in self.router.parse(path, file_url=file_url):
            try:
                multi_extract = getattr(self.extractor, "extract_documents", None)
                documents = multi_extract(raw) if multi_extract else [self.extractor.extract(raw)]
            except ExtractionError as exc:
                warnings = ["LLM_EXTRACTION_FAILED"]
                if isinstance(exc, ExtractionTimeoutError):
                    warnings.append("LLM_TIME_BUDGET_EXHAUSTED")
                else:
                    warnings.append("LLM_SCHEMA_VALIDATION_FAILED")
                operations.append(
                    self._unresolved_operation(
                        raw,
                        ExtractedDocument(),
                        accounting_entity,
                        ["model extraction exhausted its retry/time budget"],
                        warnings,
                    )
                )
                continue
            if not documents:
                operations.append(
                    self._unresolved_operation(
                        raw, ExtractedDocument(), accounting_entity, [], ["NO_CHARGES_EXTRACTED"]
                    )
                )
            for original in documents:
                document_index += 1
                planned = self._plan_document(
                    raw, original, accounting_entity, staged, seen, document_index
                )
                operations.extend(planned)
        # Validate both public and selected output before any repository mutation on disk.
        serialize_operations(operations, self.settings.json_output_indent, "assignment")
        if self.settings.output_schema_mode != "assignment":
            serialize_operations(
                operations, self.settings.json_output_indent, self.settings.output_schema_mode
            )
        if should_apply and any(
            op.operation in {Operation.CREATE, Operation.UPDATE} for op in operations
        ):
            staged.save()
            self.repository.bills = staged.all()
            self.repository.version = staged.version
        return operations

    def _plan_document(self, raw, original, accounting_entity, staged, seen, document_index):
        normalization = self.normalizer.normalize_document_with_audit(original)
        extracted = normalization.document
        validation = validate_document(extracted, self.settings.currencies_path)
        if not extracted.charges:
            return [
                self._unresolved_operation(
                    raw,
                    extracted,
                    accounting_entity,
                    [],
                    validation.errors + extracted.review_reasons,
                )
            ]
        matcher = BillMatcher(staged.all())
        operations = []
        for index, charge in enumerate(extracted.charges):
            alias = normalization.charge_resolutions[index]
            decision = matcher.decide(extracted, charge, original_document=original)
            existing = decision.matched_bill
            context = extracted.model_copy(deep=True)
            if existing:
                context.issuer = context.issuer or existing.transaction_party.issuer
                context.recipient = context.recipient or existing.transaction_party.recipient
            posting, accounting_reasoning = classify_posting(context, accounting_entity)
            warnings = list(extracted.warnings) + validation.warnings + alias.warnings
            errors = validation.errors + extracted.review_reasons + charge.review_reasons
            if extracted.extraction_schema_version:
                if staged.version is None:
                    errors.append("HISTORY_SOURCE_UNAVAILABLE")
                if "[TRUNCATED]" in raw.content:
                    errors.append("INPUT_TRUNCATED")
                if "UNKNOWN_CHARGE_ALIAS" in alias.warnings:
                    errors.append("UNKNOWN_CHARGE_REQUIRES_REVIEW")
            low_confidence = not extracted.extraction_schema_version and (
                charge.confidence is None
                or charge.confidence < self.settings.min_auto_accept_confidence
            )
            if existing and decision.operation in {Operation.UPDATE, Operation.NO_CHANGE}:
                document_info = existing.document_info.model_copy(deep=True)
                parties = existing.transaction_party.model_copy(deep=True)
                details = existing.charge_details.model_copy(deep=True)
                for field, values in decision.changed_fields.items():
                    setattr(details, field, values["new"])
            else:
                document_info = DocumentInfo(
                    document_type=extracted.document_type,
                    reference_no=extracted.reference_no,
                    document_date=extracted.document_date,
                )
                parties = TransactionParty(issuer=extracted.issuer, recipient=extracted.recipient)
                details = ChargeDetails(
                    charge_item=charge.charge_item or charge.original_charge_item,
                    amount=charge.amount,
                    currency=charge.currency,
                    payment_term=charge.payment_term,
                )
            if details.amount is None or not details.currency or not details.charge_item:
                errors.append("REQUIRED_FINANCIAL_FIELDS_MISSING")
            if extracted.extraction_schema_version and not any(
                normalize_identifier(value)
                for value in (
                    extracted.reference_no,
                    extracted.master_bill_of_lading_no,
                    extracted.house_bill_of_lading_no,
                    extracted.booking_no,
                    *extracted.container_numbers,
                )
            ):
                errors.append("DOCUMENT_IDENTITY_MISSING")
            if existing and existing.financial_attributes:
                old_entity = existing.financial_attributes.accounting_entity
                if old_entity and normalize_text(old_entity) != normalize_text(accounting_entity):
                    errors.append("ACCOUNTING_ENTITY_CONFLICT")
            if errors or alias.requires_review or low_confidence or posting is None:
                decision.operation = Operation.PENDING_REVIEW
                warnings.extend(errors)
                if low_confidence:
                    warnings.append("LOW_EXTRACTION_CONFIDENCE")
                if posting is None:
                    warnings.append("AR_AP_UNRESOLVED")
            fingerprint = self._fingerprint(extracted, charge)
            previous = seen.get(fingerprint)
            if previous and previous[0] != document_index:
                if previous[1] == Operation.PENDING_REVIEW:
                    decision.operation = Operation.PENDING_REVIEW
                elif decision.operation != Operation.PENDING_REVIEW:
                    decision.operation = Operation.NO_CHANGE
                decision.reasoning.append("duplicate charge already appeared in this batch")
                warnings.append("DUPLICATE_IN_CURRENT_BATCH")
            seen[fingerprint] = (document_index, decision.operation)
            operation = BillOperation(
                operation=decision.operation,
                document_info=document_info,
                transaction_party=parties,
                charge_details=details,
                financial_attributes=FinancialAttributes(
                    accounting_entity=accounting_entity,
                    posting_type=posting,
                ),
                audit_trail=AuditTrail(
                    source_file=raw.source_name,
                    source_page=charge.source_page,
                    original_charge_item=charge.original_charge_item,
                    source_text=charge.source_text,
                    file_hash=raw.file_hash,
                    matched_bill_id=existing.bill_id if existing else None,
                    match_strategy=decision.strategy,
                    changed_fields=decision.changed_fields,
                    conflicts=decision.conflicts,
                    confidence=charge.confidence,
                    schema_version=extracted.extraction_schema_version,
                    evidence=[
                        entry
                        for entry in extracted.evidence
                        if not entry["field_path"].startswith("/charges/")
                        or entry["field_path"].startswith(f"/charges/{index}/")
                    ],
                    observations=extracted.observations,
                    processed_at=datetime.now(UTC).isoformat(),
                    ai_reasoning=decision.reasoning + accounting_reasoning + alias.reasoning,
                    warnings=sorted(set(warnings)),
                ),
            )
            operations.append(operation)
        # Each logical document uses one history snapshot: its distinct same-name fee
        # lines must not match newly staged lines from that very document.
        for operation in operations:
            self._apply(operation, extracted, repository=staged)
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
            "date": document.document_date,
            "payment_term": charge.payment_term,
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

    def _apply(
        self,
        operation: BillOperation,
        document: ExtractedDocument,
        *,
        repository: JsonBillRepository,
    ) -> None:
        if operation.operation not in {Operation.CREATE, Operation.UPDATE}:
            return
        bill_id = operation.audit_trail.matched_bill_id or f"BILL-{uuid.uuid4().hex[:12].upper()}"
        existing = None
        if operation.operation == Operation.UPDATE:
            existing = next((item for item in repository.all() if item.bill_id == bill_id), None)
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
        bill.audit_history.append(operation.audit_trail.model_copy(deep=True))
        repository.upsert(bill)
