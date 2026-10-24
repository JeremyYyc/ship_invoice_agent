class ShipInvoiceAgentError(Exception):
    """Base application error."""


class ConfigurationError(ShipInvoiceAgentError):
    """Raised when runtime configuration is invalid."""


class UnsupportedDocumentError(ShipInvoiceAgentError):
    """Raised when a document type cannot be parsed."""


class OCRServiceError(ShipInvoiceAgentError):
    """Raised when the OCR black box fails."""


class ExtractionError(ShipInvoiceAgentError):
    """Raised when the LLM cannot produce a valid structured result."""


class ExtractionTimeoutError(ExtractionError):
    """Raised when LLM extraction exhausts its timeout/time budget."""
