"""Заглушки, пустые разделы, незаполненные чек-листы и блоки согласования (жёсткие сигналы)."""

from __future__ import annotations

import re

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.base import is_heading, iter_tables, iter_text_lines

# «XXX» в масках номеров и «будут описаны отдельным ТЗ» (исключение из объёма) — не заглушки
PLACEHOLDER_RE = re.compile(
    r"\bTBD\b|\bTBA\b|\bTODO\b|\bFIXME\b|\?\?\?"
    r"|заполня(?:ется|ются)(?:\s+позже)?|будут?\s+(?:заполнен|добавлен|описан|уточнен)\w*\s+(?:позже|потом|дополнительно|на\s+следующ)"
    r"|подлежит\s+уточнению|уточняется|на\s+уточнении|не\s+заполнен\w*|не\s+определен[оа]?\s+пока"
    r"|<\s*(?:указать|заполнить|описать)[^>]*>|\[\s*(?:указать|заполнить|описать)[^\]]*\]",
    re.IGNORECASE,
)
EMPTY_VALUES = {"", "—", "-", "–", "n/a", "нет данных", "tbd", "?"}
CHOICE_RE = re.compile(r"\bДа\s*/\s*Нет\b", re.IGNORECASE)
INLINE_EMPTY_RE = re.compile(r"\b([A-Za-zА-Яа-яЁё][\w ]{1,24}?)\s*:\s*[—–]\s*(?=$|[.;,|])")
IMPORTANT_KEY_RE = re.compile(r"url|prod|swagger|openapi|endpoint|адрес|контракт", re.IGNORECASE)
APPROVAL_RE = re.compile(r"согласу|утвержд|подпис", re.IGNORECASE)


def check(doc: SourceDocument) -> list[Signal]:
    signals: list[Signal] = []
    section_flagged: set[int] = set()

    # пустые и незаполненные разделы
    for section in doc.sections[1:]:
        if doc.children(section):
            continue
        own = [ln for ln in doc.section_lines(section, own_only=True)[1:] if ln.text.strip()]
        if not own:
            signals.append(_section_signal(doc, section, "Раздел пуст: содержание отсутствует."))
            section_flagged.add(section.id)
        elif all(PLACEHOLDER_RE.search(ln.text) for ln in own) and len(own) <= 3:
            quote = clean_snippet(own[0].text)
            signals.append(_section_signal(doc, section, f"Раздел не заполнен — вместо содержания заглушка «{quote}».", own[0].no, quote))
            section_flagged.add(section.id)

    # заглушки в тексте
    for line, _ in iter_text_lines(doc):
        if line.section_id in section_flagged or is_heading(line.text):
            continue
        m = PLACEHOLDER_RE.search(line.text)
        if m:
            signals.append(
                Signal(
                    rule_id="R-PLACEHOLDER",
                    title="Заглушка вместо содержания",
                    criterion=Criterion.COMPLETE,
                    category=Category.FORMAT,
                    severity=Severity.MINOR,
                    hard=True,
                    line=line.no,
                    quote=clean_snippet(line.text),
                    message=f"Заглушка вместо содержания: «{m.group(0)}».",
                    recommendation="Заполнить фрагмент до передачи документа в разработку.",
                )
            )
        for m in INLINE_EMPTY_RE.finditer(line.text):
            key = m.group(1).strip()
            important = bool(IMPORTANT_KEY_RE.search(key))
            signals.append(
                Signal(
                    rule_id="R-EMPTY-VALUE",
                    title=f"Не заполнено значение «{key}»",
                    criterion=Criterion.COMPLETE,
                    category=Category.FORMAT,
                    severity=Severity.MAJOR if important else Severity.MINOR,
                    hard=True,
                    line=line.no,
                    quote=clean_snippet(line.text),
                    message=f"Значение «{key}» не заполнено (стоит прочерк).",
                    recommendation=f"Указать значение «{key}» или явно написать, почему оно не требуется.",
                )
            )

    for table in iter_tables(doc):
        if table.section_id in section_flagged:
            continue
        section = doc.section(table.section_id)
        # блок согласующих
        if APPROVAL_RE.search(section.title) or any(APPROVAL_RE.search(h) for h in table.header):
            if table.rows and all(all(c.strip() in EMPTY_VALUES for c in cells) for _, cells in table.rows):
                signals.append(
                    Signal(
                        rule_id="R-APPROVALS",
                        title="Блок согласующих не заполнен",
                        criterion=Criterion.CONFORMING,
                        category=Category.FORMAT,
                        severity=Severity.MINOR,
                        hard=True,
                        line=table.header_line,
                        quote=" | ".join(table.header),
                        message=f"Блок «{section.title}» не заполнен: нет подразделений, ответственных и подписей.",
                        recommendation="Указать согласующих (подразделение, ФИО, дата) до утверждения документа.",
                    )
                )
                continue
        # чек-лист с невыбранными «Да / Нет»
        choice_rows = [(no, cells) for no, cells in table.rows if sum(bool(CHOICE_RE.search(c)) for c in cells) >= 1]
        if len(choice_rows) >= 2:
            first_no, first_cells = choice_rows[0]
            signals.append(
                Signal(
                    rule_id="R-CHECKLIST",
                    title="Чек-лист ревью не заполнен",
                    criterion=Criterion.CONFORMING,
                    category=Category.FORMAT,
                    severity=Severity.MINOR,
                    hard=True,
                    line=first_no,
                    quote=next((c for c in first_cells if c and not CHOICE_RE.search(c)), first_cells[0]),
                    message=f"Чек-лист «{section.title}» не заполнен: в {len(choice_rows)} пунктах не выбран вариант «Да / Нет».",
                    recommendation="Пройти чек-лист ревью и проставить отметки с примечаниями.",
                )
            )
            continue
        # таблица «поле — значение» с пустыми значениями
        if len(table.header) == 2:
            for no, cells in table.rows:
                if len(cells) == 2 and cells[0] and cells[1].strip().lower() in EMPTY_VALUES:
                    key = cells[0]
                    important = bool(IMPORTANT_KEY_RE.search(key))
                    signals.append(
                        Signal(
                            rule_id="R-EMPTY-VALUE",
                            title=f"Не заполнено поле «{key}»",
                            criterion=Criterion.COMPLETE,
                            category=Category.FORMAT,
                            severity=Severity.MAJOR if important else Severity.MINOR,
                            hard=True,
                            line=no,
                            quote=clean_snippet(doc.lines[no - 1].text),
                            message=f"Поле «{key}» не заполнено.",
                            recommendation=f"Заполнить поле «{key}» или указать, что оно не применимо и почему.",
                        )
                    )
    return signals


def _section_signal(doc, section, message: str, line: int | None = None, quote: str | None = None) -> Signal:
    return Signal(
        rule_id="R-EMPTY-SECTION",
        title=f"Раздел «{section.title}» не заполнен",
        criterion=Criterion.SET_COMPLETE,
        category=Category.FORMAT,
        severity=Severity.MAJOR,
        hard=True,
        line=line or section.line_start,
        quote=quote or section.title,
        message=f"«{section.title}»: {message}",
        recommendation="Заполнить раздел содержательно или удалить его, если он не нужен, указав причину.",
    )
