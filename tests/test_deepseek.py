"""Провайдер DeepSeek: настройки, запрос к OpenAI-совместимому API и разбор ответа — без сети."""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest
from pydantic import BaseModel

from reqcheck.config import DEEPSEEK_BASE_URL, Settings, review_batch_size
from reqcheck.errors import ConfigError, LLMRetriableError
from reqcheck.llm import make_provider
from reqcheck.llm.base import LLMRequest


class _Out(BaseModel):
    x: int


def _req() -> LLMRequest:
    return LLMRequest(step="review#1", system="s", user="u", schema=_Out, max_output_tokens=64000)


def _deepseek(**kw) -> Settings:
    return Settings(provider="deepseek", deepseek_api_key="test-key", **kw)


class _Completions:
    def __init__(self, replies: list):
        self.replies = list(replies)
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def _reply(content: str, completion: int = 100, reasoning: int = 40):
    return SimpleNamespace(
        model="deepseek-flash",
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
        usage=SimpleNamespace(
            prompt_tokens=10,
            completion_tokens=completion,
            completion_tokens_details=SimpleNamespace(reasoning_tokens=reasoning),
        ),
    )


def _bad_request(message: str) -> openai.BadRequestError:
    http = pytest.importorskip("httpx2")  # HTTP-клиент, на котором построен openai SDK
    request = http.Request("POST", f"{DEEPSEEK_BASE_URL}/chat/completions")
    return openai.BadRequestError(message, response=http.Response(400, request=request), body=None)


def _provider(replies: list, **kw):
    provider = make_provider(_deepseek(**kw))
    completions = _Completions(replies)
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    return provider, completions


def test_defaults():
    s = _deepseek()
    s.validate()
    assert s.model == "deepseek-flash"
    assert (s.output_tokens, s.timeout_s) == (131072, 900)
    assert Settings(provider="gemini").timeout_s == 300
    assert Settings(provider="openai").output_tokens == 32768  # 8192 обрезал ответы проверки


def test_requires_key():
    with pytest.raises(ConfigError, match="DEEPSEEK_API_KEY"):
        Settings(provider="deepseek").validate()


def test_provider_chosen_by_key_when_not_set(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    assert Settings.load().provider == "deepseek"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    assert Settings.load().provider == "gemini"


def test_model_flag_sets_deepseek_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    monkeypatch.setenv("DEEPSEEK_MODEL", "")
    assert Settings.load(model="deepseek-v4-pro").model == "deepseek-v4-pro"


def test_reserve_models_of_other_vendor_are_skipped():
    s = _deepseek(fallback_models=("gemini-3.8-flash", "deepseek-v4-pro", "deepseek-flash"))
    assert s.reserve_models == ["deepseek-v4-pro"]
    g = Settings(provider="gemini", gemini_model="gemini-3.6-flash", fallback_models=("gemini-3.8-flash", "deepseek-flash"))
    assert g.reserve_models == ["gemini-3.8-flash"]


def test_batches_of_eight_without_daily_quota():
    assert review_batch_size(_deepseek(), 55) == 8
    assert review_batch_size(Settings(provider="gemini"), 55) == 14  # у Gemini — не больше 4 вызовов


def test_request_asks_json_object_with_schema_in_text():
    provider, completions = _provider([_reply('{"x": 1}')])
    result = provider.generate(_req())
    sent = completions.calls[0]
    assert provider.name == "deepseek" and provider.base_url == DEEPSEEK_BASE_URL
    assert sent["response_format"] == {"type": "json_object"}
    assert "JSON" in sent["messages"][1]["content"]  # JSON-режим DeepSeek требует слова json в запросе
    assert sent["max_tokens"] == 64000 and "extra_body" not in sent
    assert (result.tokens_out, result.tokens_thinking) == (60, 40)


def test_thinking_off_disables_deepseek_thinking():
    provider, completions = _provider([_reply('{"x": 1}')], thinking="off")
    provider.generate(_req())
    assert completions.calls[0]["extra_body"] == {"thinking": {"type": "disabled"}}


def test_blank_answer_is_retriable():
    provider, _ = _provider([_reply("   \n ")])
    with pytest.raises(LLMRetriableError):
        provider.generate(_req())


def test_max_tokens_lowered_to_server_limit():
    error = _bad_request("Error code: 400 - Invalid max_tokens value, the valid range of max_tokens is [1, 32768]")
    provider, completions = _provider([error, _reply('{"x": 1}')])
    provider.generate(_req())
    assert [c["max_tokens"] for c in completions.calls] == [64000, 32768]
