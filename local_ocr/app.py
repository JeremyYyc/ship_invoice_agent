import os

from fastapi import FastAPI, HTTPException

from local_ocr.engine import recognize_url
from local_ocr.schemas import OCRRequest, OCRResponse

app = FastAPI(title="Local OCR Black Box", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/process", response_model=OCRResponse)
def process(request: OCRRequest) -> OCRResponse:
    try:
        pages = recognize_url(request.file, os.getenv("LOCAL_OCR_LANGUAGE", "eng"))
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return OCRResponse(
        success=True,
        task=request.task,
        pages=pages,
        full_text="\n\n".join(page.text for page in pages),
    )
