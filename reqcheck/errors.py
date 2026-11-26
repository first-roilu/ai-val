"""Исключения агента с кодами выхода CLI."""


class ReqcheckError(Exception):
    exit_code = 1


class ConfigError(ReqcheckError):
    exit_code = 2


class InputError(ReqcheckError):
    exit_code = 3


class LLMError(ReqcheckError):
    exit_code = 4


class LLMRetriableError(LLMError):
    def __init__(self, message: str, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    """Модель не ответила после всех повторов (перегрузка, сеть): можно перейти на резервную."""


class LLMFatalError(LLMError):
    """Повтор не поможет: неверный ключ, модель не найдена, некорректный запрос."""


class LLMModelNotFound(LLMFatalError):
    """Модель недоступна для этого ключа: можно перейти на резервную."""


class LLMQuotaExhausted(LLMError):
    """Исчерпана дневная квота: продолжать прогон бессмысленно."""


class LLMTruncatedError(LLMError):
    """Ответ обрезан по лимиту токенов."""


class LLMOutputError(LLMError):
    """Ответ не прошёл проверку схемы даже после ремонта."""
