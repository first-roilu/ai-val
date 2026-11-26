"""Обёртки вокруг провайдера: Tracing(Caching(Repairing(Retrying(RateLimited(Provider)))))."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import threading
import time
import typing
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from reqcheck.errors import LLMModelNotFound, LLMOutputError, LLMQuotaExhausted, LLMRetriableError, LLMUnavailable
from reqcheck.llm.base import LLMClient, LLMRequest, LLMResult
from reqcheck.llm.schema import schema_text

Notify = Callable[[str], None]


def _noop(_: str) -> None:
    pass


# ───────────────────────────── разбор JSON ─────────────────────────────

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_SCHEMA_WORDS = {"type", "properties", "required", "title", "description", "additionalProperties", "$schema"}


def _escape_stray_quotes(text: str, limit: int = 60) -> str | None:
    """Экранирует прямые кавычки внутри строк, которые модель оставила как есть («кнопка "Создать"»):
    при ошибке «ожидалась запятая или двоеточие» виновата кавычка прямо перед местом ошибки."""
    for _ in range(limit):
        try:
            json.loads(text)
            return text
        except json.JSONDecodeError as exc:
            if not exc.msg.startswith(("Expecting ',' delimiter", "Expecting ':' delimiter")):
                return None
            quote = text.rfind('"', 0, exc.pos)
            if quote <= 0 or text[quote - 1] == "\\":
                return None
            text = text[:quote] + '\\"' + text[quote + 1 :]
    return None


def _load_object(raw: str):
    text = _FENCE_RE.sub("", raw or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("ответ не содержит JSON-объекта")
    try:
        # первый целый объект: текст или второй объект после него (так отвечает DeepSeek) отбрасываются
        return json.JSONDecoder().raw_decode(text, start)[0]
    except json.JSONDecodeError:
        pass
    body = text[start : end + 1]
    fixed = _escape_stray_quotes(body)
    return json.loads(fixed if fixed is not None else body)


def _is_list_field(field) -> bool:
    return typing.get_origin(field.annotation) in (list, tuple)


def _shape(obj, schema: type[BaseModel]):
    """Возвращает данные на место, если модель положила их не туда: повторила форму JSON-схемы
    ({"type": "object", "properties": {…}}), завернула в лишний ключ ({"result": {…}}) или прислала
    голый список вместо объекта с единственным полем-списком."""
    fields = schema.model_fields
    for _ in range(3):
        if isinstance(obj, list):
            if len(fields) == 1 and _is_list_field(next(iter(fields.values()))):
                return {next(iter(fields)): obj}
            return obj
        if not isinstance(obj, dict):
            return obj
        inner = obj.get("properties")
        if set(obj) <= _SCHEMA_WORDS and isinstance(inner, dict) and set(inner) & set(fields):
            obj = inner
            continue
        if not set(obj) & set(fields):
            nested = [v for v in obj.values() if isinstance(v, dict) and set(v) & set(fields)]
            if len(nested) == 1:
                obj = nested[0]
                continue
        return obj
    return obj


def parse_json(raw: str, schema: type[BaseModel]) -> BaseModel:
    return schema.model_validate(_shape(_load_object(raw), schema))


def salvage_json(raw: str, schema: type[BaseModel], max_drops: int = 25) -> tuple[BaseModel, list[str]] | None:
    """Спасает ответ, в котором невалидны отдельные элементы списков: удаляет их и проверяет заново.

    Ошибка в элементе списка (например, сценарий без обязательного поля) не должна стоить повторного
    вызова модели на несколько минут. Возвращает (данные, пути удалённых элементов) или None, если
    ошибка не в элементе списка (тогда нужен ремонт).
    """
    try:
        obj = _shape(_load_object(raw), schema)
    except (ValueError, json.JSONDecodeError):
        return None
    dropped: list[str] = []
    for _ in range(max_drops):
        try:
            return schema.model_validate(obj), dropped
        except ValidationError as exc:
            paths = []
            for err in exc.errors():
                loc = list(err.get("loc", ()))
                idx = max((i for i, part in enumerate(loc) if isinstance(part, int)), default=None)
                if idx is None:
                    return None  # ошибка на верхнем уровне — спасать нечего
                paths.append(tuple(loc[: idx + 1]))
            # удаляем с конца, чтобы индексы соседних элементов не сдвигались
            for path in sorted(set(paths), key=lambda p: [str(x) if not isinstance(x, int) else f"{x:08d}" for x in p], reverse=True):
                node = obj
                try:
                    for part in path[:-1]:
                        node = node[part]
                    if isinstance(node, list) and 0 <= path[-1] < len(node):
                        node.pop(path[-1])
                        dropped.append(".".join(str(x) for x in path))
                except (KeyError, IndexError, TypeError):
                    return None
            if len(dropped) > max_drops:
                return None
    return None


# ───────────────────────────── ограничение частоты ─────────────────────────────


class RateLimiter:
    """Равномерный интервал между запросами: 60 / RPM секунд, общий для всех потоков."""

    def __init__(self, rpm: int):
        self.interval = 60.0 / max(rpm, 1)
        self._lock = threading.Lock()
        self._next = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next = max(now, self._next) + self.interval


class RateLimited:
    def __init__(self, inner: LLMClient, limiter: RateLimiter):
        self.inner, self.limiter = inner, limiter
        self.name, self.model = inner.name, inner.model

    def generate(self, req: LLMRequest) -> LLMResult:
        self.limiter.acquire()
        return self.inner.generate(req)


# ───────────────────────────── повторы ─────────────────────────────


class Retrying:
    def __init__(self, inner: LLMClient, max_attempts: int = 6, notify: Notify = _noop):
        self.inner, self.max_attempts, self.notify = inner, max_attempts, notify
        self.name, self.model = inner.name, inner.model

    def generate(self, req: LLMRequest) -> LLMResult:
        for attempt in range(1, self.max_attempts + 1):
            try:
                result = self.inner.generate(req)
                result.attempts = attempt
                return result
            except LLMRetriableError as exc:
                if attempt == self.max_attempts:
                    raise LLMUnavailable(f"{req.step}: не удалось получить ответ за {attempt} попыток — {exc}") from exc
                delay = exc.retry_after if exc.retry_after else min(60.0, 2.0**attempt) + random.uniform(0, 1)
                self.notify(f"{req.step}: {exc} — повтор через {delay:.0f} с (попытка {attempt + 1}/{self.max_attempts})")
                time.sleep(delay)
        raise AssertionError("unreachable")


# ───────────────────────────── ремонт JSON ─────────────────────────────


class Repairing:
    def __init__(self, inner: LLMClient, max_repairs: int = 2, notify: Notify = _noop):
        self.inner, self.max_repairs, self.notify = inner, max_repairs, notify
        self.name, self.model = inner.name, inner.model

    def generate(self, req: LLMRequest) -> LLMResult:
        current = req
        tokens_in = tokens_out = tokens_thinking = 0
        last_error = ""
        for round_no in range(self.max_repairs + 1):
            result = self.inner.generate(current)
            tokens_in += result.tokens_in
            tokens_out += result.tokens_out
            tokens_thinking += result.tokens_thinking
            try:
                result.data = parse_json(result.raw, req.schema)
            except (ValueError, ValidationError) as exc:
                salvaged = salvage_json(result.raw, req.schema)
                if salvaged is not None:
                    result.data, dropped = salvaged
                    self.notify(f"{req.step}: отброшены невалидные элементы ответа ({len(dropped)}): {', '.join(dropped[:5])}")
                    result.tokens_in, result.tokens_out, result.tokens_thinking = (
                        tokens_in, tokens_out, tokens_thinking,
                    )
                    return result
                last_error = _short_error(exc)
                self.notify(f"{req.step}: ответ не прошёл проверку схемы ({last_error[:120]}) — прошу исправить")
                current = req.with_user(
                    req.user
                    + "\n\n## Исправь ответ\n"
                    + "Предыдущий ответ не прошёл проверку JSON-схемы. Ошибки:\n"
                    + last_error
                    + "\n\nПредыдущий ответ (фрагмент):\n"
                    + (result.raw or "")[:6000]
                    + "\n\nВерни ответ целиком заново, строго по схеме:\n"
                    + schema_text(req.schema)
                )
                continue
            result.tokens_in, result.tokens_out, result.tokens_thinking = tokens_in, tokens_out, tokens_thinking
            return result
        raise LLMOutputError(f"{req.step}: ответ модели не соответствует схеме после {self.max_repairs} исправлений: {last_error[:300]}")


def _short_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        parts = []
        for err in exc.errors()[:15]:
            loc = ".".join(str(x) for x in err.get("loc", ()))
            parts.append(f"- {loc}: {err.get('msg')}")
        return "\n".join(parts)
    return str(exc)


# ───────────────────────────── кэш ─────────────────────────────


class Caching:
    """Дисковый кэш валидированных ответов. read=False — не читать (запись остаётся: можно продолжить прогон)."""

    def __init__(self, inner: LLMClient, cache_dir: Path, identity: tuple, read: bool = True):
        self.inner, self.cache_dir, self.identity, self.read = inner, Path(cache_dir), identity, read
        self.name, self.model = inner.name, inner.model

    def key(self, req: LLMRequest) -> str:
        # лимит токенов в ключ не входит: валидный ответ остаётся валидным и при другом лимите
        payload = json.dumps(
            [list(self.identity), req.step.split("#")[0], req.system, req.user, schema_text(req.schema)],
            ensure_ascii=False,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def lookup(self, req: LLMRequest) -> LLMResult | None:
        """Ответ из кэша без обращения к модели (None — промах или чтение кэша выключено)."""
        key = self.key(req)
        path = self.cache_dir / key[:2] / f"{key}.json"
        if self.read and path.is_file():
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                try:
                    data = parse_json(payload["raw"], req.schema)
                except ValidationError:
                    salvaged = salvage_json(payload["raw"], req.schema)
                    if salvaged is None:
                        raise
                    data = salvaged[0]
                return LLMResult(
                    raw=payload["raw"],
                    data=data,
                    model=payload.get("model", self.model),
                    tokens_in=payload.get("tokens_in", 0),
                    tokens_out=payload.get("tokens_out", 0),
                    tokens_thinking=payload.get("tokens_thinking", 0),
                    cache_hit=True,
                )
            except (OSError, ValueError, KeyError, ValidationError):
                pass
        return None

    def generate(self, req: LLMRequest) -> LLMResult:
        cached = self.lookup(req)
        if cached is not None:
            return cached
        key = self.key(req)
        path = self.cache_dir / key[:2] / f"{key}.json"
        result = self.inner.generate(req)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
            tmp.write_text(
                json.dumps(
                    {
                        "raw": result.raw,
                        "model": result.model,
                        "tokens_in": result.tokens_in,
                        "tokens_out": result.tokens_out,
                        "tokens_thinking": result.tokens_thinking,
                        "step": req.step,
                        "created": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            os.replace(tmp, path)
        except OSError:
            pass
        return result


# ───────────────────────────── резервные модели ─────────────────────────────


class Fallback:
    """Цепочки моделей одного провайдера: если основная недоступна, запрос уходит следующей.

    Переход происходит, когда модель не ответила после всех повторов (перегрузка, сеть), исчерпала
    квоту или не найдена для этого ключа. Переход «липкий»: остаток прогона идёт на новой модели,
    чтобы не тратить время на повторы к перегруженной. Неверный ключ и некорректный запрос
    не переключают модель — с ними не справится и резервная.
    """

    SWITCH_ON = (LLMUnavailable, LLMQuotaExhausted, LLMModelNotFound)

    def __init__(self, chains: list[LLMClient], notify: Notify = _noop):
        if not chains:
            raise ValueError("нужна хотя бы одна модель")
        self.chains, self.notify = chains, notify
        self.name, self.model = chains[0].name, chains[0].model
        self.active = 0
        self.switches: list[str] = []
        self._lock = threading.Lock()

    def generate(self, req: LLMRequest) -> LLMResult:
        # сначала кэш всех моделей: повторный прогон не должен тратить квоту, даже если в прошлый раз
        # этот шаг выполнила резервная модель
        for chain in self.chains:
            lookup = getattr(chain, "lookup", None)
            cached = lookup(req) if lookup else None
            if cached is not None:
                return cached
        index = self.active
        while True:
            chain = self.chains[index]
            try:
                return chain.generate(req)
            except self.SWITCH_ON as exc:
                if index + 1 >= len(self.chains):
                    raise
                with self._lock:
                    if self.active <= index:
                        self.active = index + 1
                        nxt = self.chains[self.active].model
                        note = f"модель {chain.model} недоступна ({type(exc).__name__}) — переключаюсь на {nxt}"
                        self.switches.append(f"{req.step}: {note}")
                        self.notify(f"{req.step}: {note}")
                    index = self.active


# ───────────────────────────── трасса ─────────────────────────────


class Tracing:
    def __init__(self, inner: LLMClient, trace):
        self.inner, self.trace = inner, trace
        self.name, self.model = inner.name, inner.model

    def generate(self, req: LLMRequest) -> LLMResult:
        started = time.monotonic()
        try:
            result = self.inner.generate(req)
        except Exception as exc:
            self.trace.record_call(req, None, time.monotonic() - started, error=f"{type(exc).__name__}: {exc}")
            raise
        result.latency_s = time.monotonic() - started
        self.trace.record_call(req, result, result.latency_s)
        return result
