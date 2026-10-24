import base64
import json
from typing import Any

_BINARY_KEYS = {"image", "image_base64", "base64", "binary", "bytes"}


class LocalOCRAdapter:
    """Convert the known local OCR response into one non-duplicated text stream."""

    def __init__(self, max_chars: int):
        self.max_chars = max_chars

    def to_llm_content(self, raw: dict[str, Any] | list[Any]) -> str:
        if isinstance(raw, dict):
            page_content = self._page_content(raw.get("pages"))
            if page_content:
                return self._truncate(page_content)

            full_text = raw.get("full_text")
            if isinstance(full_text, str) and full_text.strip():
                return self._truncate(full_text.strip())

        return self._truncate(
            json.dumps(self._clean_unknown(raw), ensure_ascii=False, separators=(",", ":"))
        )

    def _page_content(self, pages: Any) -> str:
        if not isinstance(pages, list):
            return ""

        blocks: list[str] = []
        for index, page in enumerate(pages, start=1):
            if not isinstance(page, dict):
                continue
            text = page.get("text")
            if not isinstance(text, str) or not text.strip():
                lines = page.get("lines")
                if isinstance(lines, list):
                    text = "\n".join(
                        line.strip() for line in lines if isinstance(line, str) and line.strip()
                    )
            if not isinstance(text, str) or not text.strip():
                continue
            page_number = page.get("page_number", index)
            blocks.append(f"[PAGE {page_number}]\n{text.strip()}")
        return "\n\n".join(blocks)

    def _clean_unknown(self, value: Any, key: str | None = None) -> Any:
        if key and key.lower() in _BINARY_KEYS and isinstance(value, str):
            return "[binary content omitted]"
        if isinstance(value, dict):
            return {str(k): self._clean_unknown(v, str(k)) for k, v in value.items()}
        if isinstance(value, list):
            return [self._clean_unknown(item) for item in value]
        if isinstance(value, str) and len(value) > 4096:
            try:
                base64.b64decode(value[:1024], validate=True)
                return "[probable base64 content omitted]"
            except Exception:
                return value
        return value

    def _truncate(self, content: str) -> str:
        if len(content) <= self.max_chars:
            return content
        return content[: self.max_chars] + "\n[TRUNCATED]"
