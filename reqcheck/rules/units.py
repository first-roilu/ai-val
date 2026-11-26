"""Денежные единицы и валюты (мягкие сигналы)."""

from __future__ import annotations

import re

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.base import iter_text_lines

MINOR_UNIT_RE = re.compile(r"\bтиын\w*|\bкопе[ея]\w*|\bцент\w*\b", re.IGNORECASE)
MAJOR_UNIT_RE = re.compile(r"\bтенге\b|₸|\bKZT\b|\bрубл\w*|\bдоллар\w*", re.IGNORECASE)
# «$» только рядом с числом: $BASE, $TOKEN в примерах curl — это переменные, а не доллары
FOREIGN_RE = re.compile(r"\$\s?\d|\d\s?\$|\bUSD\b|€|\bEUR\b|\bRUB\b|\bдоллар\w*|\bевро\b", re.IGNORECASE)
RATE_RE = re.compile(r"курс", re.IGNORECASE)


def check(doc: SourceDocument) -> list[Signal]:
    minor_lines, major_lines, foreign_lines = [], [], []
    has_rate = False
    for line, _ in iter_text_lines(doc):
        if MINOR_UNIT_RE.search(line.text):
            minor_lines.append(line)
        if MAJOR_UNIT_RE.search(line.text):
            major_lines.append(line)
        if FOREIGN_RE.search(line.text):
            foreign_lines.append(line)
        if RATE_RE.search(line.text):
            has_rate = True
    out: list[Signal] = []
    if minor_lines and major_lines:
        line = minor_lines[0]
        out.append(
            Signal(
                rule_id="R-UNITS",
                criterion=Criterion.SET_CONSISTENT,
                category=Category.UNITS,
                severity=Severity.MAJOR,
                hard=False,
                line=line.no,
                quote=clean_snippet(line.text),
                message="Суммы встречаются и в минимальных единицах (тиыны/копейки), и в основных — проверьте правило пересчёта и сравнения.",
                recommendation="Зафиксировать единицу хранения и сравнения сумм и правило пересчёта (1 ₸ = 100 тиын), округление.",
            )
        )
    if foreign_lines and major_lines and not has_rate:
        line = foreign_lines[0]
        out.append(
            Signal(
                rule_id="R-CURRENCY",
                criterion=Criterion.SET_CONSISTENT,
                category=Category.UNITS,
                severity=Severity.MAJOR,
                hard=False,
                line=line.no,
                quote=clean_snippet(line.text),
                message="В документе несколько валют, но курс и момент пересчёта не указаны.",
                recommendation="Указать курс (источник, дату) и правило пересчёта между валютами.",
            )
        )
    return out
