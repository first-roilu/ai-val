"""Провайдер для OpenAI-совместимых API: OpenAI, OpenRouter, Groq, DeepSeek, Ollama, llama.cpp, vLLM."""

from __future__ import annotations

import re
import time

from reqcheck.errors import (
    LLMFatalError,
    LLMModelNotFound,
    LLMOutputError,
    LLMQuotaExhausted,
    LLMRetriableError,
    LLMTruncatedError,
)
from reqcheck.llm.base import LLMRequest, LLMResult
from reqcheck.llm.schema import json_schema, schema_text

_MODES = ("json_schema", "json_object", "prompt")
_NUMBER_RE = re.compile(r"\d+")


def _token_cap(message: str, requested: int) -> int | None:
    """Предел длины ответа из текста ошибки сервера («at most 16384», «range of max_tokens is [1, 8192]»):
    наибольшее число меньше запрошенного. Мелкие числа вроде кода ошибки 400 не считаются."""
    candidates = [n for n in map(int, _NUMBER_RE.findall(message)) if 1024 <= n < requested]
    return max(candidates) if candidates else None


class OpenAICompatProvider:
    name = "openai"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        temperature: float,
        seed: int | None,
        thinking: str,
        timeout_s: int,
        name: str = "openai",
        json_mode: str = "json_schema",
        extra_body: dict | None = None,
    ):
        from openai import OpenAI

        self.name = name
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.thinking = thinking
        self.base_url = base_url
        self._mode = _MODES.index(json_mode)
        self._extra_body = extra_body
        self._seed_ok = seed is not None
        self._max_tokens_param = "max_tokens" if base_url else "max_completion_tokens"
        self._token_cap: int | None = None  # предел сервера, если он меньше запрошенного
        self.client = OpenAI(api_key=api_key or "not-needed", base_url=base_url or None, timeout=timeout_s, max_retries=0)

    def _max_tokens(self, req: LLMRequest) -> int:
        return min(req.max_output_tokens, self._token_cap) if self._token_cap else req.max_output_tokens

    def _kwargs(self, req: LLMRequest) -> dict:
        mode = _MODES[self._mode]
        user = req.user
        if mode != "json_schema":
            user += (
                "\n\n## Формат ответа\nВерни только JSON-объект с данными по схеме — не саму схему и без пояснений. "
                f"Ключи верхнего уровня: {', '.join(req.schema.model_fields)}. "
                "Внутри строк вместо прямых кавычек используй «ёлочки».\nСхема:\n" + schema_text(req.schema)
            )
        kwargs: dict = {
            "model": self.model,
            "messages": [{"role": "system", "content": req.system}, {"role": "user", "content": user}],
            "temperature": self.temperature,
            self._max_tokens_param: self._max_tokens(req),
        }
        if self._extra_body:
            kwargs["extra_body"] = self._extra_body
        if self._seed_ok:
            kwargs["seed"] = self.seed
        if mode == "json_schema":
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": req.schema.__name__, "schema": json_schema(req.schema), "strict": False},
            }
        elif mode == "json_object":
            kwargs["response_format"] = {"type": "json_object"}
        if self.thinking in ("minimal", "low", "medium", "high"):
            kwargs["reasoning_effort"] = self.thinking
        return kwargs

    def generate(self, req: LLMRequest) -> LLMResult:
        import openai

        started = time.monotonic()
        response = None
        for _ in range(6):
            try:
                response = self.client.chat.completions.create(**self._kwargs(req))
                break
            except openai.BadRequestError as exc:
                message = str(exc).lower()
                if "reasoning_effort" in message and self.thinking != "default":
                    self.thinking = "default"
                    continue
                if "seed" in message and self._seed_ok:
                    self._seed_ok = False
                    continue
                if "max_tokens" in message or "max_completion_tokens" in message:
                    if "unsupported" in message:
                        self._max_tokens_param = "max_completion_tokens" if self._max_tokens_param == "max_tokens" else "max_tokens"
                        continue
                    cap = _token_cap(message, self._max_tokens(req))
                    if cap:
                        self._token_cap = cap
                        continue
                if ("response_format" in message or "json" in message or "schema" in message) and self._mode < len(_MODES) - 1:
                    self._mode += 1
                    continue
                raise LLMFatalError(f"Провайдер отклонил запрос: {str(exc)[:300]}") from exc
            except openai.RateLimitError as exc:
                message = str(exc)
                if "quota" in message.lower() and "exceeded" in message.lower():
                    raise LLMQuotaExhausted(f"Исчерпана квота провайдера: {message[:200]}") from exc
                retry_after = None
                try:
                    retry_after = float(exc.response.headers.get("retry-after"))
                except (AttributeError, TypeError, ValueError):
                    pass
                raise LLMRetriableError("лимит запросов (429)", retry_after=retry_after) from exc
            except (openai.APIConnectionError, openai.APITimeoutError, openai.InternalServerError) as exc:
                raise LLMRetriableError(f"{type(exc).__name__}") from exc
            except openai.AuthenticationError as exc:
                raise LLMFatalError("Провайдер отклонил ключ: проверьте OPENAI_API_KEY") from exc
            except openai.NotFoundError as exc:
                raise LLMModelNotFound(f"Модель {self.model!r} или адрес {self.base_url or 'api.openai.com'} не найдены") from exc
        if response is None:
            raise LLMFatalError("Провайдер не принял ни один вариант параметров запроса")

        choice = response.choices[0] if response.choices else None
        text = (choice.message.content or "") if choice and choice.message else ""
        finish = (choice.finish_reason or "") if choice else ""
        if finish == "length":
            raise LLMTruncatedError(f"{req.step}: ответ обрезан по лимиту {req.max_output_tokens} токенов")
        if not text.strip() and finish == "content_filter":
            raise LLMOutputError(f"{req.step}: ответ заблокирован фильтром провайдера")
        if not text.strip():
            # DeepSeek в JSON-режиме заменяет нераспознанный ответ пробелами — это повод повторить запрос
            raise LLMRetriableError("пустой ответ провайдера")
        usage = response.usage
        completion = getattr(usage, "completion_tokens", 0) or 0
        details = getattr(usage, "completion_tokens_details", None)
        reasoning = (getattr(details, "reasoning_tokens", 0) or 0) if details is not None else 0
        return LLMResult(
            raw=text,
            model=getattr(response, "model", None) or self.model,
            tokens_in=getattr(usage, "prompt_tokens", 0) or 0,
            tokens_out=max(0, completion - reasoning),  # как у Gemini: ответ отдельно от «размышлений»
            tokens_thinking=reasoning,
            latency_s=time.monotonic() - started,
            finish_reason=finish,
        )
