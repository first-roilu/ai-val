"""Настройки: файл .env → переменные окружения → флаги CLI (в порядке возрастания приоритета)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path

from dotenv import load_dotenv

from reqcheck.errors import ConfigError

PROJECT_ROOT = Path(__file__).resolve().parent.parent

PROVIDERS = ("gemini", "deepseek", "openai", "fake")
THINKING_LEVELS = ("default", "off", "minimal", "low", "medium", "high")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_DEFAULT_MODEL = "deepseek-flash"
# ID моделей начинаются с имени поставщика: резерв gemini-* не уходит в API DeepSeek и наоборот
_MODEL_PREFIX = {"gemini": "gemini", "deepseek": "deepseek"}


@dataclass
class Settings:
    provider: str = "gemini"
    gemini_api_key: str = ""
    gemini_model: str = ""
    deepseek_api_key: str = ""
    deepseek_model: str = ""
    openai_api_key: str = ""
    openai_base_url: str = ""
    openai_model: str = ""
    fallback_models: tuple[str, ...] = ()
    temperature: float = 0.0
    seed: int | None = 42
    max_rpm: int = 10
    concurrency: int = 1
    thinking: str = "default"
    batch_size: int = 0  # 0 — подбирается по числу требований, чтобы уложиться в 4 вызова проверки
    max_output_tokens: int = 0  # 0 — значение по умолчанию для провайдера
    request_timeout_s: int = 0  # 0 — значение по умолчанию для провайдера
    cache_dir: Path = field(default_factory=lambda: Path(".cache/reqcheck"))
    runs_dir: Path = field(default_factory=lambda: Path("runs"))
    use_cache: bool = True
    quick: bool = False
    replay_dir: Path | None = None  # воспроизведение: ответы модели из трассы прогона, модель не вызывается

    @property
    def model(self) -> str:
        return {
            "gemini": self.gemini_model,
            "deepseek": self.deepseek_model or DEEPSEEK_DEFAULT_MODEL,
            "openai": self.openai_model,
            "fake": "fake-heuristic",
        }.get(self.provider, "")

    @property
    def reserve_models(self) -> list[str]:
        """Резервные модели: без основной и без моделей другого поставщика."""
        if self.provider == "fake" or self.replay_dir is not None:
            return []
        prefix = _MODEL_PREFIX.get(self.provider)
        return [m for m in self.fallback_models if m != self.model and (prefix is None or m.lower().startswith(prefix))]

    @property
    def output_tokens(self) -> int:
        if self.max_output_tokens:
            return self.max_output_tokens
        # ответ проверки вместе с «размышлениями» занимает десятки тысяч токенов: меньший лимит его обрежет;
        # если сервер принимает меньше, OpenAI-совместимый провайдер снизит лимит по тексту ошибки
        # DeepSeek рассуждает до 40 тыс. токенов на пачку проверки и считает их в лимите ответа
        return {"gemini": 60000, "deepseek": 131072}.get(self.provider, 32768)

    @property
    def timeout_s(self) -> int:
        if self.request_timeout_s:
            return self.request_timeout_s
        return 900 if self.provider == "deepseek" else 300  # DeepSeek под нагрузкой отвечает дольше, но не отказывает

    @classmethod
    def load(cls, **overrides) -> "Settings":
        for env_file in (Path.cwd() / ".env", PROJECT_ROOT / ".env"):
            if env_file.is_file():
                load_dotenv(env_file, override=False, encoding="utf-8")
        env = os.environ
        s = cls(
            provider=(env.get("LLM_PROVIDER") or "").strip().lower() or _auto_provider(env),
            gemini_api_key=env.get("GEMINI_API_KEY", "").strip(),
            gemini_model=env.get("GEMINI_MODEL", "").strip(),
            deepseek_api_key=env.get("DEEPSEEK_API_KEY", "").strip(),
            deepseek_model=env.get("DEEPSEEK_MODEL", "").strip(),
            openai_api_key=env.get("OPENAI_API_KEY", "").strip(),
            openai_base_url=env.get("OPENAI_BASE_URL", "").strip(),
            openai_model=env.get("OPENAI_MODEL", "").strip(),
            fallback_models=_list(env.get("LLM_FALLBACK_MODELS")),
            temperature=_float(env.get("LLM_TEMPERATURE"), 0.0),
            seed=_int_or_none(env.get("LLM_SEED"), 42),
            max_rpm=_int(env.get("LLM_MAX_RPM"), 10),
            concurrency=_int(env.get("LLM_CONCURRENCY"), 1),
            thinking=env.get("LLM_THINKING", "default").strip().lower(),
            batch_size=_int(env.get("REQCHECK_BATCH_SIZE"), 0),
            max_output_tokens=_int(env.get("LLM_MAX_OUTPUT_TOKENS"), 0),
            request_timeout_s=_int(env.get("LLM_TIMEOUT_S"), 0),
            cache_dir=Path(env.get("REQCHECK_CACHE_DIR", ".cache/reqcheck")),
            runs_dir=Path(env.get("REQCHECK_RUNS_DIR", "runs")),
        )
        names = {f.name for f in fields(cls)}
        for key, value in overrides.items():
            if value is None:
                continue
            if key == "model":
                setattr(s, {"openai": "openai_model", "deepseek": "deepseek_model"}.get(s.provider, "gemini_model"), value)
                continue
            if key not in names:
                raise ConfigError(f"Неизвестная настройка: {key}")
            setattr(s, key, value)
        s.provider = s.provider.lower()
        return s

    def validate(self) -> None:
        if self.provider not in PROVIDERS:
            raise ConfigError(f"LLM_PROVIDER={self.provider!r} не поддерживается. Допустимо: {', '.join(PROVIDERS)}.")
        if self.thinking not in THINKING_LEVELS:
            raise ConfigError(f"LLM_THINKING={self.thinking!r} не поддерживается. Допустимо: {', '.join(THINKING_LEVELS)}.")
        if self.max_rpm < 1 or self.concurrency < 1 or self.batch_size < 0:
            raise ConfigError("LLM_MAX_RPM и LLM_CONCURRENCY должны быть ≥ 1, REQCHECK_BATCH_SIZE ≥ 0 (0 — авто).")
        if self.replay_dir is not None:  # ответы берутся из трассы: ключ модели не нужен
            return
        if self.provider == "gemini":
            if not self.gemini_api_key:
                raise ConfigError(
                    "Не задан GEMINI_API_KEY. Получите бесплатный ключ на https://aistudio.google.com/apikey, "
                    "скопируйте .env.example в .env и впишите ключ."
                )
            if not self.gemini_model:
                raise ConfigError("Не задан GEMINI_MODEL. Укажите ID модели из AI Studio в .env (см. .env.example).")
        if self.provider == "deepseek" and not self.deepseek_api_key:
            raise ConfigError(
                "Не задан DEEPSEEK_API_KEY. Создайте ключ на https://platform.deepseek.com/api_keys "
                "и впишите его в .env (см. .env.example)."
            )
        if self.provider == "openai":
            if not self.openai_model:
                raise ConfigError("Не задан OPENAI_MODEL для OpenAI-совместимого провайдера.")
            if not self.openai_api_key and not self.openai_base_url:
                raise ConfigError("Не задан OPENAI_API_KEY (или OPENAI_BASE_URL для локального сервера).")


def _auto_provider(env) -> str:
    """LLM_PROVIDER не задан: провайдер по ключу — DeepSeek, если задан его ключ, иначе Gemini."""
    return "deepseek" if (env.get("DEEPSEEK_API_KEY") or "").strip() else "gemini"


def _list(value: str | None) -> tuple[str, ...]:
    return tuple(dict.fromkeys(x.strip() for x in (value or "").split(",") if x.strip()))


def _int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError as exc:
        raise ConfigError(f"Ожидалось целое число, получено {value!r}") from exc


def _int_or_none(value: str | None, default: int | None) -> int | None:
    if value is None:
        return default
    if value.strip().lower() in ("", "none", "off"):
        return None
    return _int(value, default or 0)


def _float(value: str | None, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except ValueError as exc:
        raise ConfigError(f"Ожидалось число, получено {value!r}") from exc


def review_batch_size(settings: "Settings", n_requirements: int) -> int:
    """Размер пачки проверки. У Gemini авто — не больше 4 вызовов (бесплатный тариф — около 20 запросов
    в день на модель), но не больше 18 требований в пачке, чтобы ответ не упирался в лимит токенов.
    У DeepSeek и других OpenAI-совместимых API дневной квоты нет, а вход дешёвый: пачки по 8 требований —
    модель разбирает каждое подробнее, лишние вызовы стоят доли цента."""
    if settings.batch_size:
        return settings.batch_size
    if settings.provider in ("deepseek", "openai"):
        return 15 if settings.quick else 8
    calls = 2 if settings.quick else 4
    return max(8, min(18 if not settings.quick else 30, -(-n_requirements // calls)))
