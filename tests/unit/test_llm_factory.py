from ship_invoice_agent.config import Settings
from ship_invoice_agent.llm import factory


def test_factory_disables_provider_retries(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "ChatOpenAI", fake_chat_openai)
    settings = Settings(
        llm_api_key="test-key",
        llm_timeout_seconds=60,
        llm_total_time_budget_seconds=130,
        llm_max_retries=1,
    )

    factory.create_chat_model(settings)

    assert captured["timeout"] == 60
    assert captured["max_retries"] == 0
    assert captured["extra_body"] == {"enable_thinking": False}


def test_factory_does_not_send_qwen_extension_to_other_providers(monkeypatch):
    captured = {}

    def fake_chat_openai(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(factory, "ChatOpenAI", fake_chat_openai)
    settings = Settings(
        llm_provider="openai",
        llm_model_name="gpt-example",
        llm_api_key="test-key",
        llm_enable_thinking=False,
    )

    factory.create_chat_model(settings)

    assert "extra_body" not in captured
