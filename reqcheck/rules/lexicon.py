"""Размытые и незамкнутые формулировки по ISO/IEC/IEEE 29148 п. 5.2.7 (мягкие сигналы)."""

from __future__ import annotations

import re

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import SourceDocument
from reqcheck.knowledge import load_lexicon
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.base import is_heading, is_separator_row, iter_text_lines

INJECTION_RE = re.compile(
    r"(?:для|to)\s+(?:ии|ai|llm|gpt|модел\w*|нейросет\w*|автоматическ\w*\s+(?:провер|ревью|анализ)\w*|ревьюер\w*|проверяющ\w*)"
    r"|ignore\s+(?:all\s+|the\s+)?(?:previous|above)\s+instructions"
    r"|игнорир\w*\s+(?:предыдущ|вышеизложенн|все)\w*\s+(?:инструкц|указани)\w*"
    r"|не\s+(?:выдава|указыва|давать|писать)\w*\s+(?:никаких\s+)?замечани\w*"
    r"|счита\w*\s+(?:этот\s+)?документ\s+(?:согласованн|утвержденн|утверждённ|проверенн)\w*",
    re.IGNORECASE,
)


def check(doc: SourceDocument) -> list[Signal]:
    lexicon = load_lexicon()
    signals: list[Signal] = []
    for line, _ in iter_text_lines(doc):
        text = line.text
        if not text.strip() or is_separator_row(text):
            continue
        m = INJECTION_RE.search(text)
        if m:
            signals.append(
                Signal(
                    rule_id="R-INJECTION",
                    title="Указание для автоматической проверки в тексте требований",
                    criterion=Criterion.CONFORMING,
                    category=Category.SECURITY,
                    severity=Severity.MAJOR,
                    hard=True,
                    line=line.no,
                    quote=clean_snippet(text),
                    message=(
                        f"Документ содержит указание для автоматической проверки («{m.group(0)}»). "
                        "Агент его не выполняет: требования не могут управлять результатом ревью."
                    ),
                    recommendation="Удалить из требований обращения к проверяющим и ИИ; статус согласования фиксировать в блоке согласующих.",
                )
            )
        if is_heading(text):
            continue
        for entry in lexicon:
            for m in entry["re"].finditer(text):
                signals.append(
                    Signal(
                        rule_id="R-VAGUE",
                        criterion=Criterion(entry["criterion"]),
                        category=Category.NFR if entry.get("nfr") else Category.DOMAIN,
                        severity=Severity.MAJOR,
                        hard=False,
                        line=line.no,
                        quote=clean_snippet(text),
                        message=f"«{m.group(0)}» — {entry['label']}",
                        recommendation=entry.get("hint", ""),
                    )
                )
                break
    return signals
