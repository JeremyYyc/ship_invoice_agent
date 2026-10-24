import shutil
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

from ship_invoice_agent.config import Settings
from ship_invoice_agent.exceptions import OCRServiceError
from ship_invoice_agent.ocr.local_adapter import LocalOCRAdapter


class OCRClient:
    """Black-box OCR client using exactly the request contract from the assignment."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.adapter = LocalOCRAdapter(settings.ocr_max_response_chars)

    def stage_file(self, source: Path) -> tuple[Path, str]:
        root = self.settings.local_file_server_root
        root.mkdir(parents=True, exist_ok=True)
        target = root / source.name
        if source.resolve() != target.resolve():
            shutil.copy2(source, target)
        url = f"{self.settings.local_file_base_url}/{quote(target.name)}"
        return target, url

    def recognize(self, file_url: str) -> dict[str, Any] | list[Any]:
        payload = {
            "file": file_url,
            "task": self.settings.ocr_task,
            "callBack": self.settings.ocr_callback,
            "taskType": self.settings.ocr_task_type,
        }
        last_error: Exception | None = None
        for attempt in range(self.settings.ocr_max_retries + 1):
            try:
                response = requests.post(
                    self.settings.ocr_api_url,
                    json=payload,
                    timeout=(
                        self.settings.ocr_connect_timeout_seconds,
                        self.settings.ocr_read_timeout_seconds,
                    ),
                )
                response.raise_for_status()
                result = response.json()
                if not isinstance(result, (dict, list)):
                    raise OCRServiceError("OCR response JSON must be an object or array")
                return result
            except (requests.RequestException, ValueError, OCRServiceError) as exc:
                last_error = exc
                if attempt < self.settings.ocr_max_retries:
                    time.sleep(min(2**attempt, 4))
        raise OCRServiceError(f"OCR request failed: {last_error}") from last_error

    def sanitize_for_llm(self, raw: dict[str, Any] | list[Any]) -> str:
        """Adapt local OCR output, with a safe raw-JSON fallback for unknown schemas."""

        return self.adapter.to_llm_content(raw)
