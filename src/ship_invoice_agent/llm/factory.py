from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from ship_invoice_agent.config import Settings


def create_chat_model(settings: Settings) -> BaseChatModel:
    settings.require_llm_credentials()
    common = {
        "model": settings.llm_model_name,
        "temperature": settings.llm_temperature,
        "timeout": settings.llm_timeout_seconds,
        # Retry policy is owned by InvoiceExtractor so one configured retry cannot
        # multiply with a provider SDK's own retry loop.
        "max_retries": 0,
        "max_tokens": settings.llm_max_tokens,
    }
    if settings.llm_protocol == "openai_compatible":
        provider_options = {}
        is_qwen = (
            settings.llm_provider.casefold() == "qwen"
            or settings.llm_model_name.casefold().startswith("qwen")
        )
        if is_qwen:
            provider_options["extra_body"] = {
                "enable_thinking": settings.llm_enable_thinking
            }
        return ChatOpenAI(
            **common,
            **provider_options,
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
        )
    return init_chat_model(model_provider=settings.llm_provider, **common)
