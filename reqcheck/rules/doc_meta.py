"""Метаданные документа: версия и дата (жёсткий сигнал)."""

from __future__ import annotations

import re

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.placeholders import PLACEHOLDER_RE

VERSION_RE = re.compile(r"верси[яи]\s*(?:документа)?\s*[:№]?\s*\d|version\s*:?\s*\d|\bv\d+\.\d+|редакци[яи]\s*[:№]?\s*\d", re.IGNORECASE)
DATE_RE = re.compile(
    r"\b\d{2}\.\d{2}\.\d{4}\b|\b\d{4}-\d{2}-\d{2}\b"
    r"|\b\d{1,2}\s+(?:январ|феврал|март|апрел|ма[яй]|июн|июл|август|сентябр|октябр|ноябр|декабр)\w*\s+\d{4}",
    re.IGNORECASE,
)


def check(doc: SourceDocument) -> list[Signal]:
    text = doc.text
    if VERSION_RE.search(text) or DATE_RE.search(text):
        return []
    # уже есть заглушка про версию — отдельный сигнал не нужен
    for line in doc.lines:
        if re.search(r"верси", line.text, re.IGNORECASE) and PLACEHOLDER_RE.search(line.text):
            return []
    first = next((ln for ln in doc.lines if ln.text.strip()), doc.lines[0])
    return [
        Signal(
            rule_id="R-VERSION",
            title="Нет версии и даты документа",
            criterion=Criterion.SET_MODIFIABLE,
            category=Category.FORMAT,
            severity=Severity.MINOR,
            hard=True,
            line=first.no,
            quote=clean_snippet(first.text),
            message="В документе не указаны версия и дата — нельзя однозначно сослаться на проверенную редакцию.",
            recommendation="Добавить в шапку версию, дату и автора; вести историю изменений.",
        )
    ]
