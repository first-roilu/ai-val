"""Провайдер Google Gemini (google-genai)."""

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
from reqcheck.llm.schema import json_schema

_RETRY_DELAY_RE = re.compile(r"retry[_ ]?delay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", re.IGNORECASE)
_BLOCKED = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


def _is_network_error(exc: Exception) -> bool:
    names = " ".join(cls.__name__ for cls in type(exc).__mro__)
    return any(token in names for token in ("Timeout", "Connect", "Network", "RemoteProtocol", "ReadError", "WriteError"))


class GeminiProvider:
    name = "gemini"

    def __init__(self, *, api_key: str, model: str, temperature: float, seed: int | None, thinking: str, timeout_s: int):
        from google import genai
        from google.genai import types

        self._types = types
        self._genai = genai
        self._api_key = api_key
        self._timeout_ms = timeout_s * 1000
        self.model = model
        self.temperature = temperature
        self.seed = seed
        self.thinking = thinking
        self._thinking_ok = thinking != "default"
        self._json_schema_ok = True
        self._api_version: str | None = None
        self.client = self._make_client()

    def _make_client(self):
        options = {"timeout": self._timeout_ms}
        if self._api_version:
            options["api_version"] = self._api_version
        return self._genai.Client(api_key=self._api_key, http_options=self._types.HttpOptions(**options))

    def _config(self, req: LLMRequest):
        t = self._types
        kwargs: dict = {
            "system_instruction": req.system,
            "temperature": self.temperature,
            "max_output_tokens": req.max_output_tokens,
            "response_mime_type": "application/json",
        }
        if self.seed is not None:
            kwargs["seed"] = self.seed
        if self._json_schema_ok:
            kwargs["response_json_schema"] = json_schema(req.schema)
        else:
            kwargs["response_schema"] = req.schema
        if self._thinking_ok:
            if self.thinking == "off":
                kwargs["thinking_config"] = t.ThinkingConfig(thinking_budget=0)
            else:
                kwargs["thinking_config"] = t.ThinkingConfig(thinking_level=self.thinking.upper())
        return t.GenerateContentConfig(**kwargs)

    def generate(self, req: LLMRequest) -> LLMResult:
        from google.genai import errors

        started = time.monotonic()
        response = None
        for _ in range(4):  # внутренние откаты для неподдерживаемых опций
            try:
                response = self.client.models.generate_content(model=self.model, contents=req.user, config=self._config(req))
                break
            except errors.ClientError as exc:
                message = str(exc)
                lowered = message.lower()
                if exc.code == 400 and "v1beta" in lowered and self._api_version != "v1beta":
                    # часть моделей (например, gemini-2.5-flash) принимает системную инструкцию только в v1beta
                    self._api_version = "v1beta"
                    self.client = self._make_client()
                    continue
                if exc.code == 400 and "thinking" in lowered and self._thinking_ok:
                    self._thinking_ok = False
                    continue
                if exc.code == 400 and "schema" in lowered and self._json_schema_ok:
                    self._json_schema_ok = False
                    continue
                raise self._map_client_error(exc) from exc
            except errors.ServerError as exc:
                raise LLMRetriableError(f"сервер Gemini ответил {exc.code}") from exc
            except Exception as exc:
                if _is_network_error(exc):
                    raise LLMRetriableError(f"сетевая ошибка: {type(exc).__name__}") from exc
                raise
        if response is None:
            raise LLMFatalError("Gemini отклонил параметры запроса")

        candidate = response.candidates[0] if response.candidates else None
        finish = ""
        if candidate is not None and candidate.finish_reason is not None:
            finish = getattr(candidate.finish_reason, "value", str(candidate.finish_reason))
        text = response.text or ""
        if finish == "MAX_TOKENS":
            raise LLMTruncatedError(f"{req.step}: ответ обрезан по лимиту {req.max_output_tokens} токенов")
        if not text and finish in _BLOCKED:
            raise LLMOutputError(f"{req.step}: Gemini заблокировал ответ ({finish})")
        if not text:
            raise LLMRetriableError(f"пустой ответ Gemini (finish_reason={finish or 'нет'})")

        usage = response.usage_metadata
        return LLMResult(
            raw=text,
            model=getattr(response, "model_version", None) or self.model,
            tokens_in=(usage.prompt_token_count or 0) if usage else 0,
            tokens_out=(usage.candidates_token_count or 0) if usage else 0,
            tokens_thinking=(usage.thoughts_token_count or 0) if usage else 0,
            latency_s=time.monotonic() - started,
            finish_reason=finish,
        )

    def _map_client_error(self, exc) -> Exception:
        message = str(exc)
        code = exc.code
        if code == 429:
            if re.search(r"per\s*day|perday|daily", message, re.IGNORECASE):
                return LLMQuotaExhausted(
                    f"Исчерпана дневная квота Gemini для модели {self.model}. Повторите позже или укажите другую модель "
                    "(GEMINI_MODEL); выполненные шаги сохранены в кэше."
                )
            m = _RETRY_DELAY_RE.search(message)
            return LLMRetriableError("лимит запросов Gemini (429)", retry_after=float(m.group(1)) + 1 if m else None)
        if code in (401, 403):
            return LLMFatalError(f"Gemini отклонил ключ или доступ к модели (код {code}). Проверьте GEMINI_API_KEY. {message[:200]}")
        if code == 404:
            return LLMModelNotFound(f"Модель {self.model!r} не найдена. Доступные модели: {self.available_models()}")
        return LLMFatalError(f"Gemini отклонил запрос (код {code}): {message[:300]}")

    def available_models(self) -> str:
        try:
            names = []
            for m in self.client.models.list():
                actions = getattr(m, "supported_actions", None) or []
                if not actions or "generateContent" in actions:
                    names.append((m.name or "").removeprefix("models/"))
            return ", ".join(sorted(n for n in names if "gemini" in n)[:40]) or "список пуст"
        except Exception:
            return "не удалось получить список (проверьте ключ и сеть)"
