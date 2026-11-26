"""Сборка LLM-клиента: провайдер + обёртки (лимит, повторы, ремонт JSON, кэш, трасса)."""

from __future__ import annotations

from reqcheck.config import DEEPSEEK_BASE_URL, Settings
from reqcheck.llm.base import LLMClient, LLMRequest, LLMResult
from reqcheck.llm.middleware import Caching, Fallback, Notify, RateLimited, RateLimiter, Repairing, Retrying, Tracing

__all__ = ["LLMClient", "LLMRequest", "LLMResult", "build_client", "make_provider"]


def make_provider(settings: Settings, model: str | None = None) -> LLMClient:
    if settings.provider == "gemini":
        from reqcheck.llm.gemini import GeminiProvider

        return GeminiProvider(
            api_key=settings.gemini_api_key,
            model=model or settings.gemini_model,
            temperature=settings.temperature,
            seed=settings.seed,
            thinking=settings.thinking,
            timeout_s=settings.timeout_s,
        )
    if settings.provider in ("deepseek", "openai"):
        from reqcheck.llm.openai_compat import OpenAICompatProvider

        deepseek = settings.provider == "deepseek"
        return OpenAICompatProvider(
            api_key=settings.deepseek_api_key if deepseek else settings.openai_api_key,
            base_url=DEEPSEEK_BASE_URL if deepseek else settings.openai_base_url,
            model=model or settings.model,
            temperature=settings.temperature,
            seed=settings.seed,
            thinking=settings.thinking,
            timeout_s=settings.timeout_s,
            name=settings.provider,
            # DeepSeek не принимает json_schema: сразу просим JSON-объект, а схему передаём в тексте запроса
            json_mode="json_object" if deepseek else "json_schema",
            # «размышления» у DeepSeek включены по умолчанию и выключаются отдельным полем запроса
            extra_body={"thinking": {"type": "disabled"}} if deepseek and settings.thinking == "off" else None,
        )
    from reqcheck.llm.fake import FakeProvider

    return FakeProvider()


def build_client(settings: Settings, trace, notify: Notify) -> LLMClient:
    if settings.replay_dir is not None:
        from reqcheck.llm.replay import ReplayProvider

        return Tracing(Repairing(ReplayProvider(settings.replay_dir), notify=notify), trace)
    if settings.provider == "fake":
        return Tracing(Repairing(make_provider(settings), notify=notify), trace)
    models = [settings.model, *settings.reserve_models]
    chains: list[LLMClient] = []
    for i, model in enumerate(models):
        provider = make_provider(settings, model)
        chain: LLMClient = RateLimited(provider, RateLimiter(settings.max_rpm))  # лимиты у Gemini — на модель
        # с резервом впереди не ждём перегруженную модель долго
        chain = Retrying(chain, max_attempts=4 if i + 1 < len(models) else 6, notify=notify)
        chain = Repairing(chain, notify=notify)
        chain = Caching(
            chain,
            settings.cache_dir,
            identity=(provider.name, provider.model, settings.temperature, settings.seed, settings.thinking),
            read=settings.use_cache,
        )
        chains.append(chain)
    client: LLMClient = chains[0] if len(chains) == 1 else Fallback(chains, notify=notify)
    return Tracing(client, trace)
