import mimetypes
from pathlib import Path

from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import UnsupportedDocumentError
from ship_invoice_agent.models import RawDocument
from ship_invoice_agent.ocr import OCRClient
from ship_invoice_agent.parsers.common import ensure_size, sha256_file
from ship_invoice_agent.parsers.email import parse_email
from ship_invoice_agent.parsers.excel import parse_excel
from ship_invoice_agent.parsers.word import parse_docx, parse_legacy_doc

OCR_SUFFIXES = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}
EXCEL_SUFFIXES = {".xls", ".xlsx", ".xlsm"}


class DocumentRouter:
    def __init__(self, settings: Settings, ocr_client: OCRClient):
        self.settings = settings
        self.ocr_client = ocr_client

    def parse(
        self,
        path: Path,
        *,
        file_url: str | None = None,
        parent_source: str | None = None,
        depth: int = 0,
    ) -> list[RawDocument]:
        path = path.resolve()
        ensure_size(path, self.settings.max_input_file_size_mb)
        suffix = path.suffix.lower()
        file_hash = sha256_file(path)

        if suffix == ".eml":
            return self._parse_email(path, file_hash, parent_source, depth)
        if suffix in OCR_SUFFIXES:
            if not file_url:
                _, file_url = self.ocr_client.stage_file(path)
            raw_ocr = self.ocr_client.recognize(file_url)
            content = self.ocr_client.sanitize_for_llm(raw_ocr)
            return [
                RawDocument(
                    source_path=path,
                    source_name=path.name,
                    source_type=suffix.lstrip("."),
                    content=content,
                    metadata={"file_url": file_url, "ocr_raw": raw_ocr},
                    file_hash=file_hash,
                    parent_source=parent_source,
                )
            ]
        if suffix in EXCEL_SUFFIXES:
            content = parse_excel(path)
        elif suffix == ".docx":
            content = parse_docx(path)
        elif suffix == ".doc":
            content = parse_legacy_doc(path)
        else:
            guessed, _ = mimetypes.guess_type(path.name)
            raise UnsupportedDocumentError(
                f"unsupported document type {suffix or guessed}: {path.name}"
            )

        return [
            RawDocument(
                source_path=path,
                source_name=path.name,
                source_type=suffix.lstrip("."),
                content=content,
                metadata={},
                file_hash=file_hash,
                parent_source=parent_source,
            )
        ]

    def _parse_email(
        self, path: Path, file_hash: str, parent_source: str | None, depth: int
    ) -> list[RawDocument]:
        if depth > self.settings.max_email_recursion_depth:
            raise ValueError("maximum email attachment recursion depth exceeded")
        body, metadata, attachments = parse_email(path)
        if len(attachments) > self.settings.max_email_attachment_count:
            raise ValueError("maximum email attachment count exceeded")
        documents = [
            RawDocument(
                source_path=path,
                source_name=path.name,
                source_type="eml",
                content=body,
                metadata=metadata,
                file_hash=file_hash,
                parent_source=parent_source,
            )
        ]
        attachment_root = self.settings.local_file_server_root / "attachments" / file_hash[:12]
        attachment_root.mkdir(parents=True, exist_ok=True)
        for index, (filename, payload) in enumerate(attachments):
            safe_name = Path(filename).name or f"attachment-{index}"
            attachment_path = attachment_root / safe_name
            attachment_path.write_bytes(payload)
            try:
                documents.extend(
                    self.parse(
                        attachment_path,
                        parent_source=path.name,
                        depth=depth + 1,
                    )
                )
            except UnsupportedDocumentError:
                continue
        return documents
