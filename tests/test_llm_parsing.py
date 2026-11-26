"""Снисходительный разбор ответов модели: необязательные поля и спасение невалидных элементов списков."""

from __future__ import annotations

import json

from reqcheck.llm.middleware import Repairing, salvage_json
from reqcheck.llm.base import LLMRequest, LLMResult
from reqcheck.models import ReviewOut


def _review(checks: list[dict], scenarios: list[dict] | None = None) -> str:
    return json.dumps(
        {"assessments": [{"rid": "REQ-01", "scenarios": scenarios or [], "checks": checks, "findings": [], "rewrite": "", "conclusion": ""}]},
        ensure_ascii=False,
    )


def test_text_or_second_object_after_json_is_ignored():
    from reqcheck.llm.middleware import _load_object

    assert _load_object('{"x": 1}\n{"x": 2}') == {"x": 1}  # «Extra data» — без повторного вызова
    assert _load_object('Ответ: {"x": {"y": [1]}} — готово.') == {"x": {"y": [1]}}


def test_stray_quotes_inside_strings_are_escaped():
    from reqcheck.llm.middleware import _load_object

    raw = '{"title": "Кнопка "Создать" не описана", "x": ["a "b" c"]}'
    assert _load_object(raw) == {"title": 'Кнопка "Создать" не описана', "x": ['a "b" c']}


def test_schema_echo_wrapper_and_bare_list_are_unwrapped():
    from reqcheck.llm.middleware import parse_json
    from reqcheck.models import StandardsOut

    item = {"rid": "REQ-01", "scenarios": [], "checks": [], "findings": [], "rewrite": "", "conclusion": ""}
    for shape in ({"type": "object", "properties": {"assessments": [item]}}, {"result": {"assessments": [item]}}, [item]):
        assert parse_json(json.dumps(shape, ensure_ascii=False), ReviewOut).assessments[0].rid == "REQ-01"
    # поле «properties» у StandardsOut — данные, а не повтор схемы
    assert parse_json('{"findings": [], "properties": []}', StandardsOut).properties == []
    assert parse_json('{"type": "object", "properties": {"findings": [], "properties": []}}', StandardsOut).findings == []


def test_missing_optional_fields_are_filled():
    raw = _review([], scenarios=[{"given": "сумма 999", "when": "сохранить", "expected": "ошибка"}])  # без title и determinable
    out = ReviewOut.model_validate_json(raw)
    assert out.assessments[0].scenarios[0].title == "" and out.assessments[0].scenarios[0].determinable is True


def test_invalid_list_item_is_dropped_not_whole_answer():
    raw = _review([{"criterion": "ОДНОЗН", "status": "ok"}, {"criterion": "НЕТ-ТАКОГО", "status": "fail"}])
    data, dropped = salvage_json(raw, ReviewOut)
    assert [c.criterion.value for c in data.assessments[0].checks] == ["ОДНОЗН"]
    assert dropped == ["assessments.0.checks.1"]


def test_top_level_error_is_not_salvaged():
    assert salvage_json(json.dumps({"something": []}), ReviewOut) is None


class _Once:
    name, model = "stub", "stub"

    def __init__(self, raw: str):
        self.raw, self.calls = raw, 0

    def generate(self, req):
        self.calls += 1
        return LLMResult(raw=self.raw, model="stub")


def test_repairing_salvages_without_second_call():
    inner = _Once(_review([{"criterion": "ПРОВ", "status": "warn"}, {"criterion": "?", "status": "ok"}]))
    result = Repairing(inner).generate(LLMRequest(step="review#1", system="", user="", schema=ReviewOut))
    assert inner.calls == 1 and len(result.data.assessments[0].checks) == 1
