FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY local_ocr ./local_ocr
RUN pip install --no-cache-dir \
    fastapi "uvicorn[standard]" requests pdf2image Pillow pytesseract pydantic

CMD ["uvicorn", "local_ocr.app:app", "--host", "0.0.0.0", "--port", "8001"]

