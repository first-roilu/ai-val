"""Резервные модели: переключение при перегрузке, квоте и отсутствии модели; ключ не переключает."""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from reqcheck.errors import LLMFatalError, LLMModelNotFound, LLMQuotaExhausted, LLMUnavailable
from reqcheck.llm.base import LLMRequest, LLMResult
from reqcheck.llm.middleware import Fallback


class _Out(BaseModel):
    x: int


class _Stub:
    name = "stub"

    def __init__(self, model: str, errors: list[Exception] | None = None):
        self.model = model
        self.errors = list(errors or [])
        self.calls = 0

    def generate(self, req: LLMRequest) -> LLMResult:
        self.calls += 1
        if self.errors:
            raise self.errors.pop(0)
        return LLMResult(raw='{"x": 1}', model=self.model)


def _req(step: str = "review#1") -> LLMRequest:
    return LLMRequest(step=step, system="s", user="u", schema=_Out)


@pytest.mark.parametrize("error", [LLMUnavailable("503"), LLMQuotaExhausted("day"), LLMModelNotFound("404")])
def test_switches_to_next_model_and_stays_there(error):
    primary, reserve = _Stub("main", [error]), _Stub("reserve")
    notes: list[str] = []
    client = Fallback([primary, reserve], notify=notes.append)
    assert client.generate(_req()).model == "reserve"
    assert client.generate(_req("review#2")).model == "reserve"
    assert primary.calls == 1 and reserve.calls == 2  # «липкий» переход: к основной больше не ходим
    assert len(notes) == 1 and "main" in notes[0] and "reserve" in notes[0]


def test_bad_key_does_not_switch():
    primary, reserve = _Stub("main", [LLMFatalError("401")]), _Stub("reserve")
    with pytest.raises(LLMFatalError):
        Fallback([primary, reserve]).generate(_req())
    assert reserve.calls == 0


def test_last_model_error_propagates():
    client = Fallback([_Stub("a", [LLMUnavailable("503")]), _Stub("b", [LLMUnavailable("503")])])
    with pytest.raises(LLMUnavailable):
        client.generate(_req())


def test_passport_shows_reserve_models():
    from types import SimpleNamespace

    from reqcheck.report.render import _model_label

    ctx = SimpleNamespace(
        llm=SimpleNamespace(model="gemini-main"),
        settings=SimpleNamespace(model="gemini-main"),
        trace=SimpleNamespace(models={"gemini-main": 3, "gemini-reserve": 9}),
    )
    label = _model_label(ctx)
    assert label.startswith("gemini-main по настройке") and "gemini-reserve — 9" in label and "(из 12)" in label
    ctx.trace.models = {"gemini-main-001": 5}
    assert _model_label(ctx) == "gemini-main"


def test_parallel_runs_in_same_second_get_separate_trace_dirs(tmp_path):
    from datetime import datetime

    from reqcheck.trace import RunTrace

    started = datetime(2026, 11, 26, 10, 0, 0)
    a = RunTrace(tmp_path, "spec", started)
    b = RunTrace(tmp_path, "spec", started)
    assert a.dir != b.dir and a.dir.is_dir() and b.dir.is_dir()


def test_cache_of_reserve_model_is_used_before_calling_primary(tmp_path):
    from reqcheck.llm.middleware import Caching

    primary_api, reserve_api = _Stub("main"), _Stub("reserve")
    primary = Caching(primary_api, tmp_path, identity=("stub", "main"))
    reserve = Caching(reserve_api, tmp_path, identity=("stub", "reserve"))
    reserve.generate(_req())  # в прошлом прогоне шаг выполнила резервная модель
    assert reserve_api.calls == 1
    result = Fallback([primary, reserve]).generate(_req())
    assert result.cache_hit and result.model == "reserve"
    assert primary_api.calls == 0 and reserve_api.calls == 1  # квота основной не тронута


def test_auto_batch_size_caps_review_calls():
    from reqcheck.config import Settings, review_batch_size

    s = Settings()
    assert review_batch_size(s, 20) == 8
    assert review_batch_size(s, 59) == 15  # 4 вызова вместо 8
    assert review_batch_size(s, 300) == 18  # пачка не растёт бесконечно: лимит токенов ответа
    s.batch_size = 5
    assert review_batch_size(s, 59) == 5
