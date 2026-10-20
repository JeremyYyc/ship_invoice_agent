from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class RawDocument(BaseModel):
    source_path: Path
    source_name: str
    source_type: str
    content: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    file_hash: str
    parent_source: str | None = None
