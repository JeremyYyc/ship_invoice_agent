from io import BytesIO

import pytesseract
import requests
from pdf2image import convert_from_bytes
from PIL import Image

from local_ocr.schemas import OCRPage


def download(url: str) -> tuple[bytes, str]:
    if not url.startswith(("http://", "https://")):
        raise ValueError("file must be an HTTP(S) URL")
    response = requests.get(url, timeout=(10, 120))
    response.raise_for_status()
    return response.content, response.headers.get("content-type", "")


def recognize_url(url: str, language: str = "eng") -> list[OCRPage]:
    content, content_type = download(url)
    if "pdf" in content_type.lower() or content.startswith(b"%PDF"):
        images = convert_from_bytes(content, dpi=200)
    else:
        images = [Image.open(BytesIO(content)).convert("RGB")]
    pages: list[OCRPage] = []
    for index, image in enumerate(images, start=1):
        text = pytesseract.image_to_string(image, lang=language)
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        pages.append(OCRPage(page_number=index, text=text.strip(), lines=lines))
    return pages
