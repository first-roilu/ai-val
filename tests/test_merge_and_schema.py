import json

from reqcheck.llm.schema import json_schema
from reqcheck.models import (
    Category,
    Criterion,
    Evidence,
    Finding,
    Locator,
    ReviewOut,
    Severity,
    SynthesisOut,
)
from reqcheck.steps.merge import assign_ids, dedupe


def _f(title, line, criterion=Criterion.COMPLETE, severity=Severity.MAJOR, problem="", hard=False):
    return Finding(
        criterion=criterion,
        category=Category.DOMAIN,
        severity=severity,
        title=title,
        problem=problem or title,
        evidence=[Evidence(quote="q", locator=Locator(section_path="s", line_start=line, line_end=line))],
        hard=hard,
        source=["rule:X" if hard else "llm:review"],
    )


def test_dedupe_merges_same_issue_and_keeps_max_severity():
    a = _f("Раздел тестирования не заполнен", 57, Criterion.SET_COMPLETE, Severity.MAJOR, hard=True)
    b = _f("Пустой раздел требований к тестированию", 57, Criterion.SET_COMPLETE, Severity.CRITICAL)
    merged = dedupe([a, b])
    assert len(merged) == 1
    assert merged[0].severity == Severity.CRITICAL and merged[0].hard


def test_dedupe_keeps_different_issues_of_same_requirement():
    a = _f("Не описан частичный возврат", 38, problem="Частичный возврат: какую сумму вычитать из расходов месяца")
    b = _f("Не определены типы операций кроме переводов", 38, problem="Квази-кэш, комиссии и платежи за услуги не классифицированы")
    assert len(dedupe([a, b])) == 2


def test_assign_ids_orders_by_severity():
    items = [_f("minor", 10, severity=Severity.MINOR), _f("critical", 50, severity=Severity.CRITICAL)]
    assign_ids(items)
    assert [(f.fid, f.title) for f in items] == [("D-001", "critical"), ("D-002", "minor")]


def test_schema_is_flat_and_fully_required():
    for model in (ReviewOut, SynthesisOut):
        schema = json_schema(model)
        text = json.dumps(schema, ensure_ascii=False)
        assert "$ref" not in text and "$defs" not in text
        item = schema["properties"][next(iter(schema["properties"]))]
        assert schema["required"] == list(schema["properties"].keys())
        assert item


def test_schema_keeps_fields_named_like_json_schema_keywords():
    from reqcheck.llm.schema import json_schema
    from reqcheck.models import FindingDraft, ScenarioDraft

    for model in (FindingDraft, ScenarioDraft):
        schema = json_schema(model)
        assert "title" in schema["properties"], model.__name__
        assert "title" in schema["required"]
        assert "title" not in schema  # служебный ключ верхнего уровня по-прежнему удалён


def test_merge_is_independent_of_draft_arrival_order(tmp_path):
    """Параллельные шаги складывают черновики в разном порядке — ID и состав находок не должны меняться."""
    import random
    from datetime import datetime

    from reqcheck.config import Settings
    from reqcheck.context import RunContext
    from reqcheck.grounding import Grounder
    from reqcheck.ingest import load_document
    from reqcheck.models import FindingDraft
    from reqcheck.steps import merge
    from reqcheck.trace import RunTrace
    from tests.conftest import REQUIREMENTS_MD

    doc = load_document(REQUIREMENTS_MD)

    def draft(title, quote, severity="MAJOR", criterion="ПОЛН"):
        return FindingDraft(rids=[], criterion=criterion, category="процесс", severity=severity, title=title,
                            quotes=[quote], problem=title + ": подробности.", recommendation="Уточнить.")

    drafts = [
        (draft("Нет часового пояса", "Месяц определяется по дате совершения покупки в Алматы."), "llm:review"),
        (draft("Возврат в следующем месяце", "Возврат уменьшает расходы месяца исходной покупки."), "llm:review"),
        (draft("Противоречие Ф-3 и Ф-4", "На карту приходится максимум один такой push за календарный месяц.",
               "CRITICAL", "Н-СОГЛ"), "llm:lens_consistency"),
        (draft("GET для сохранения", "| **REST type** | GET |", "CRITICAL", "СООТВ"), "llm:lens_standards"),
    ]

    def run_with(order):
        ctx = RunContext(settings=Settings(provider="fake"), doc=doc, grounder=Grounder(doc), llm=None,
                         trace=RunTrace(tmp_path / "runs", "t", datetime.now()), notify=lambda m: None,
                         started=datetime.now().astimezone())
        ctx.drafts = list(order)
        merge.run(ctx)
        return [(f.fid, f.title) for f in ctx.findings]

    expected = run_with(drafts)
    shuffled = drafts[:]
    random.Random(7).shuffle(shuffled)
    assert run_with(shuffled) == expected
    assert run_with(list(reversed(drafts))) == expected


def test_merged_finding_keeps_text_of_most_severe():
    from reqcheck.steps.merge import merge_group

    get = _f("GET изменяет настройку", 104, severity=Severity.CRITICAL, hard=True)
    content_type = _f("Content-Type при пустом теле", 104, severity=Severity.MINOR, problem="длинное описание мелочи")
    merged = merge_group([content_type, get])
    assert (merged.title, merged.severity) == ("GET изменяет настройку", Severity.CRITICAL)
