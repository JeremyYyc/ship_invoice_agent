from pydantic import BaseModel, Field


class OCRRequest(BaseModel):
    file: str
    task: str = "orderChange"
    callBack: str = ""
    taskType: str = ""


class OCRPage(BaseModel):
    page_number: int
    text: str
    lines: list[str] = Field(default_factory=list)


class OCRResponse(BaseModel):
    success: bool
    task: str
    pages: list[OCRPage]
    full_text: str
