from ship_invoice_agent.config import Settings
from ship_invoice_agent.ocr import OCRClient


def test_sanitizes_binary_without_assuming_provider_schema():
    settings = Settings(llm_api_key="test", ocr_max_response_chars=10000)
    client = OCRClient(settings)
    result = client.sanitize_for_llm(
        {
            "vendor_specific": {
                "content": "INVOICE 123",
                "image_base64": "aGVsbG8=",
            }
        }
    )

    assert "INVOICE 123" in result
    assert "binary content omitted" in result
