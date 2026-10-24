from ship_invoice_agent.config import Settings
from ship_invoice_agent.ocr import LocalOCRAdapter, OCRClient


def test_local_adapter_uses_each_page_text_once():
    adapter = LocalOCRAdapter(max_chars=10_000)
    result = adapter.to_llm_content(
        {
            "success": True,
            "task": "orderChange",
            "pages": [
                {
                    "page_number": 1,
                    "text": "INVOICE 123\nH/C USD 65",
                    "lines": ["INVOICE 123", "H/C USD 65"],
                },
                {
                    "page_number": 2,
                    "text": "TOTAL USD 65",
                    "lines": ["TOTAL USD 65"],
                },
            ],
            "full_text": "INVOICE 123\nH/C USD 65\n\nTOTAL USD 65",
        }
    )

    assert result == (
        "[PAGE 1]\nINVOICE 123\nH/C USD 65\n\n[PAGE 2]\nTOTAL USD 65"
    )
    assert result.count("INVOICE 123") == 1
    assert result.count("H/C USD 65") == 1
    assert result.count("TOTAL USD 65") == 1
    assert "full_text" not in result
    assert "lines" not in result


def test_local_adapter_falls_back_to_lines_then_full_text():
    adapter = LocalOCRAdapter(max_chars=10_000)

    from_lines = adapter.to_llm_content(
        {"pages": [{"page_number": 3, "text": "", "lines": ["A", "B"]}]}
    )
    from_full_text = adapter.to_llm_content({"pages": [], "full_text": "FULL DOCUMENT"})

    assert from_lines == "[PAGE 3]\nA\nB"
    assert from_full_text == "FULL DOCUMENT"


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


def test_local_adapter_truncates_canonical_text_once():
    adapter = LocalOCRAdapter(max_chars=12)

    result = adapter.to_llm_content(
        {"pages": [{"page_number": 1, "text": "ABCDEFGHIJKLMNOP"}]}
    )

    assert result == "[PAGE 1]\nABC\n[TRUNCATED]"
