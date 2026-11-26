"""Целостность текста: восстановленные и безвозвратно повреждённые фрагменты (жёсткие сигналы)."""

from __future__ import annotations

import re

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.repair import BROKEN_LABEL, FIX_LABEL
from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal

REQUIREMENT_PATH_RE = re.compile(r"требован|логик|правил|критери|параметр|статус|функц|story|истори|сценари|интеграц|данн", re.IGNORECASE)
MAX_BROKEN_SIGNALS = 15


def check(doc: SourceDocument) -> list[Signal]:
    report = doc.repair
    if report is None:
        return []
    signals: list[Signal] = []

    first_by_line: dict[int, tuple[str, str]] = {}
    for no, kind, fragment in report.broken:
        first_by_line.setdefault(no, (kind, fragment))
    items = sorted(first_by_line.items())
    for i, (no, (kind, fragment)) in enumerate(items[:MAX_BROKEN_SIGNALS]):
        path = doc.section_path_of(no)
        tail = ""
        if i == MAX_BROKEN_SIGNALS - 1 and len(items) > MAX_BROKEN_SIGNALS:
            tail = f" Всего повреждённых строк: {len(items)}."
        signals.append(
            Signal(
                rule_id="R-BROKEN-TEXT",
                title="Повреждённые символы в тексте",
                criterion=Criterion.SET_COMPREHENSIBLE,
                category=Category.FORMAT,
                severity=Severity.MAJOR if REQUIREMENT_PATH_RE.search(path) else Severity.MINOR,
                hard=True,
                line=no,
                quote=clean_snippet(doc.lines[no - 1].text),
                message=(
                    f"В строке {no} {BROKEN_LABEL[kind]}: «{fragment}». Смысл фрагмента по документу не восстановить — "
                    f"реализовать и проверить его однозначно нельзя.{tail}"
                ),
                recommendation="Запросить у автора исправленный файл или исходный текст фрагмента и перепроверить требование.",
            )
        )

    if report.fixed_lines:
        lines = sorted({n for nums in report.fixed.values() for n in nums})
        kinds = "; ".join(f"{FIX_LABEL[k]} — строк {len(set(v))}" for k, v in report.fixed.items())
        examples = "; ".join(f"строка {n}: «{before[:40]}» → «{after[:40]}»" for n, before, after in report.examples[:3])
        signals.append(
            Signal(
                rule_id="R-REPAIRED-TEXT",
                title="Текст документа был повреждён и восстановлен автоматически",
                criterion=Criterion.SET_COMPREHENSIBLE,
                category=Category.FORMAT,
                severity=Severity.MINOR,
                hard=True,
                line=lines[0],
                quote=clean_snippet(doc.lines[lines[0] - 1].text),
                message=(
                    f"Агент восстановил повреждённые фрагменты: {kinds}. Примеры: {examples}. "
                    f"Проверка выполнена по восстановленному тексту."
                ),
                recommendation="Пересохранить документ в UTF-8 или заново экспортировать PDF со встроенными шрифтами; сверить восстановленные строки с оригиналом.",
            )
        )
    return signals
