"""Идентификаторы требований: дубли, висячие ссылки, разрывы нумерации (жёсткие сигналы)."""

from __future__ import annotations

import re
from collections import defaultdict

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.base import iter_text_lines

_ID = r"([A-ZА-ЯЁ]{1,6}(?:-[A-ZА-ЯЁ]{1,6})?-\d{1,3}(?:\.\d{1,3})*)"
DEF_LINE_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+\.\s+)?(?:\*\*)?\s*" + _ID + r"\s*[.):]?\s*(?:\*\*)?(?:\s|$)")
DEF_CELL_RE = re.compile(r"^\s*\|\s*(?:\*\*)?\s*" + _ID + r"\s*(?:\*\*)?\s*\|")
MENTION_RE = re.compile(r"(?<![\w-])" + _ID + r"(?![\w-])")
IGNORED_PREFIXES = {"UTF", "SHA", "ISO", "RFC", "PCI", "TLS", "SSL", "ГОСТ", "HTTP", "COVID", "ASCII"}


def _prefix(rid: str) -> str:
    return rid.rsplit("-", 1)[0]


def check(doc: SourceDocument) -> list[Signal]:
    definitions: dict[str, list[int]] = defaultdict(list)
    texts: dict[int, str] = {}
    for line, _ in iter_text_lines(doc):
        texts[line.no] = line.text
        m = DEF_LINE_RE.match(line.text) or DEF_CELL_RE.match(line.text)
        if m and _prefix(m.group(1)) not in IGNORED_PREFIXES:
            definitions[m.group(1)].append(line.no)
    if not definitions:
        return []
    prefixes = {_prefix(rid) for rid in definitions}
    signals: list[Signal] = []

    for rid, lines in definitions.items():
        if len(lines) > 1:
            signals.append(
                Signal(
                    rule_id="R-ID-DUPLICATE",
                    title=f"Повторяющийся идентификатор {rid}",
                    criterion=Criterion.SET_TRACEABLE,
                    category=Category.TRACE,
                    severity=Severity.MAJOR,
                    hard=True,
                    line=lines[1],
                    quote=clean_snippet(texts[lines[1]]),
                    message=f"Идентификатор {rid} определён несколько раз (строки {', '.join(map(str, lines))}).",
                    recommendation=f"Сделать идентификаторы уникальными: перенумеровать повторный {rid}.",
                )
            )

    defined_lines = {no for lines in definitions.values() for no in lines}
    reported: set[str] = set()
    for no, text in texts.items():
        for m in MENTION_RE.finditer(text):
            rid = m.group(1)
            if rid in definitions or rid in reported or _prefix(rid) not in prefixes:
                continue
            if no in defined_lines and m.start() < 8:
                continue
            reported.add(rid)
            signals.append(
                Signal(
                    rule_id="R-ID-DANGLING",
                    title=f"Ссылка на несуществующее требование {rid}",
                    criterion=Criterion.SET_TRACEABLE,
                    category=Category.TRACE,
                    severity=Severity.MAJOR,
                    hard=True,
                    line=no,
                    quote=clean_snippet(text),
                    message=f"Ссылка на {rid}, но требование с таким идентификатором в документе не определено.",
                    recommendation=f"Добавить требование {rid} или исправить ссылку.",
                )
            )

    by_prefix: dict[str, list[int]] = defaultdict(list)
    first_line: dict[str, int] = {}
    for rid, lines in definitions.items():
        tail = rid.rsplit("-", 1)[1]
        if "." in tail:
            continue
        by_prefix[_prefix(rid)].append(int(tail))
        first_line.setdefault(_prefix(rid), lines[0])
    for prefix, numbers in by_prefix.items():
        present = set(numbers)
        if len(present) < 2 or max(present) > 300:
            continue
        missing = [n for n in range(min(present), max(present)) if n not in present]
        if 0 < len(missing) <= 5:
            names = ", ".join(f"{prefix}-{n}" for n in missing)
            signals.append(
                Signal(
                    rule_id="R-ID-GAP",
                    title=f"Разрыв нумерации требований {prefix}-n",
                    criterion=Criterion.SET_MODIFIABLE,
                    category=Category.TRACE,
                    severity=Severity.MINOR,
                    hard=True,
                    line=first_line[prefix],
                    quote=clean_snippet(texts[first_line[prefix]]),
                    message=f"В нумерации требований {prefix}-n пропущены: {names}.",
                    recommendation="Восстановить пропущенные пункты или указать, что они исключены и почему.",
                )
            )
    return signals
