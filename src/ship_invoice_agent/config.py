from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "ship_invoice_agent"
    app_env: str = "development"
    log_level: str = "INFO"
    json_output_indent: int | None = 2

    llm_protocol: Literal["openai_compatible", "langchain_provider"] = "openai_compatible"
    llm_provider: str = "qwen"
    llm_model_name: str = "qwen-plus"
    llm_base_url: str | None = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_api_key: SecretStr | None = None
    llm_temperature: float = 0
    llm_timeout_seconds: int = Field(default=115, gt=0)
    llm_total_time_budget_seconds: int = Field(default=235, gt=0)
    llm_max_retries: int = Field(default=1, ge=0)
    llm_max_tokens: int = 8192
    llm_enable_thinking: bool = False
    llm_structured_output_mode: Literal["prompt", "native"] = "prompt"

    ocr_api_url: str = "http://127.0.0.1:8001/process"
    ocr_task: str = "orderChange"
    ocr_callback: str = ""
    ocr_task_type: str = ""
    ocr_connect_timeout_seconds: int = 10
    ocr_read_timeout_seconds: int = 300
    ocr_max_retries: int = 2
    ocr_max_response_chars: int = 200_000

    local_file_server_host: str = "127.0.0.1"
    local_file_server_port: int = 8081
    local_file_server_root: Path = Path(".runtime/files")
    local_file_base_url: str = "http://127.0.0.1:8081"

    existing_bills_path: Path = Path("data/existing_bills.json")
    charge_aliases_path: Path = Path("data/charge_aliases.json")
    party_aliases_path: Path = Path("data/party_aliases.json")
    accounting_entities_path: Path = Path("data/accounting_entities.json")
    currencies_path: Path = Path("data/currencies.json")

    max_input_file_size_mb: int = 30
    max_email_attachment_count: int = 30
    max_email_recursion_depth: int = 3
    max_document_pages: int = 100
    default_accounting_entity: str | None = None
    apply_bill_changes: bool = False
    min_auto_accept_confidence: float = Field(default=0.85, ge=0, le=1)

    local_ocr_language: str = "eng"
    local_ocr_use_gpu: bool = False

    @field_validator("llm_base_url", "ocr_api_url", "local_file_base_url")
    @classmethod
    def validate_urls(cls, value: str | None) -> str | None:
        if value and not value.startswith(("http://", "https://")):
            raise ValueError("service URLs must start with http:// or https://")
        return value.rstrip("/") if value else value

    def require_llm_credentials(self) -> None:
        if not self.llm_model_name:
            raise ValueError("LLM_MODEL_NAME is required")
        if self.llm_protocol == "openai_compatible" and not self.llm_api_key:
            raise ValueError("LLM_API_KEY is required for live model calls")
        if self.llm_protocol == "openai_compatible" and not self.llm_base_url:
            raise ValueError("LLM_BASE_URL is required for OpenAI-compatible calls")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
