"""Контракт API: метод ↔ действие, коды ответов, типы полей, тело у GET (мягкие сигналы)."""

from __future__ import annotations

import re
from collections import defaultdict

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import FENCE_RE, SourceDocument
from reqcheck.models import Category, Criterion, Severity, Signal
from reqcheck.rules.base import iter_tables

SAFE_METHOD_RE = re.compile(r"(?:REST\s*type|HTTP[-\s]*метод|метод|method|type)\W{0,12}\b(GET|HEAD|OPTIONS)\b", re.IGNORECASE)
CURL_GET_RE = re.compile(r"\bcurl\b[^\n]*\s-G\b|\bcurl\b[^\n]*-X\s*GET\b", re.IGNORECASE)
MUTATING_URL_RE = re.compile(r"/[\w/.-]*?(save|create|update|delete|remove|set|add|change|edit|insert|register|submit)[A-Za-z]*", re.IGNORECASE)
MUTATING_TEXT_RE = re.compile(r"\b(сохранени\w*|сохраня\w*|создани\w*|изменени\w*|удалени\w*|добавлени\w*|замен\w*|обновлени\w*|регистраци\w*)\b", re.IGNORECASE)
STATUS_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+\**\s*([1-5]\d{2})\b")
STATUS_MENTION_RE = re.compile(r"(?<![\d.,])([1-5]\d{2})(?![\d.,%])")
STATUS_SECTION_RE = re.compile(r"статус|status|ответ|response|код", re.IGNORECASE)
ERROR_BODY_RE = re.compile(r'"(?:ERROR|FAIL\w*|error)"|"result"\s*:\s*"(?!OK)|ошибк', re.IGNORECASE)
NAME_COL_RE = re.compile(r"^(parameter|параметр|поле|field|name|имя|атрибут)$", re.IGNORECASE)
TYPE_COL_RE = re.compile(r"^(format|формат|тип|type)$", re.IGNORECASE)


def check(doc: SourceDocument) -> list[Signal]:
    signals: list[Signal] = []
    signals += _safe_method_mutation(doc)
    signals += _status_codes(doc)
    signals += _type_mismatch(doc)
    return signals


def _safe_method_mutation(doc: SourceDocument) -> list[Signal]:
    out: list[Signal] = []
    for line in doc.lines:
        m = SAFE_METHOD_RE.search(line.text) or CURL_GET_RE.search(line.text)
        if not m:
            continue
        method = m.group(1).upper() if m.groups() and m.group(1) else "GET"
        top = _top_section(doc, line.section_id)
        scope = [ln for ln in doc.lines if _top_section(doc, ln.section_id) == top]
        url_hits = {mm.group(0) for ln in scope for mm in MUTATING_URL_RE.finditer(ln.text)}
        text_hits = {mm.group(0).lower() for ln in scope[:40] for mm in MUTATING_TEXT_RE.finditer(ln.text)}
        if not url_hits and not text_hits:
            continue
        evidence = ", ".join(sorted(url_hits)[:2] + sorted(text_hits)[:2])
        out.append(
            Signal(
                rule_id="R-HTTP-METHOD",
                criterion=Criterion.FEASIBLE,
                category=Category.API,
                severity=Severity.CRITICAL,
                hard=False,
                line=line.no,
                quote=clean_snippet(line.text),
                message=f"Безопасный метод {method} указан для операции, изменяющей данные ({evidence}). RFC 9110 п. 9.2.1.",
                recommendation="Использовать POST/PUT/PATCH, параметры передавать в теле запроса.",
            )
        )
        break
    for line in doc.lines:
        if re.search(r"content-type", line.text, re.IGNORECASE) and re.search(r"application/json", line.text, re.IGNORECASE):
            near = " ".join(ln.text for ln in doc.lines[line.no : line.no + 3])
            if re.search(r"не\s+использу|не\s+передаёт|не\s+передает|отсутству", near, re.IGNORECASE):
                out.append(
                    Signal(
                        rule_id="R-BODY",
                        criterion=Criterion.SET_CONSISTENT,
                        category=Category.API,
                        severity=Severity.MINOR,
                        hard=False,
                        line=line.no,
                        quote=clean_snippet(line.text),
                        message="Указан Content-Type тела запроса, но тело не используется.",
                        recommendation="Убрать Content-Type или описать тело запроса.",
                    )
                )
    return out


def _top_section(doc: SourceDocument, section_id: int) -> int:
    section = doc.section(section_id)
    while section.parent not in (None, 0) and doc.section(section.parent).level > 1:
        section = doc.section(section.parent)
    return section.id


def _status_codes(doc: SourceDocument) -> list[Signal]:
    out: list[Signal] = []
    declared: dict[str, int] = {}
    current_code: str | None = None
    in_code = False
    error_bodies: list[tuple[int, str, str]] = []
    mentions: dict[str, int] = {}
    for line in doc.lines:
        if FENCE_RE.match(line.text):
            in_code = not in_code
            continue
        heading = STATUS_HEADING_RE.match(line.text)
        if heading and not in_code:
            current_code = heading.group(1)
            declared.setdefault(current_code, line.no)
            continue
        if re.match(r"^\s{0,3}#{1,6}\s", line.text) and not in_code:
            current_code = None
        if in_code and current_code and current_code.startswith("2") and ERROR_BODY_RE.search(line.text):
            error_bodies.append((line.no, current_code, clean_snippet(line.text)))
        if not in_code and STATUS_SECTION_RE.search(doc.section(line.section_id).path):
            for m in STATUS_MENTION_RE.finditer(re.sub(r"`[^`]*`|\d+\s*(?:bit|бит|знак)", "", line.text)):
                code = m.group(1)
                if code.startswith(("2", "3", "4", "5")) and int(code) in range(200, 600):
                    mentions.setdefault(code, line.no)
    for no, code, quote in error_bodies[:3]:
        out.append(
            Signal(
                rule_id="R-STATUS-2XX-ERROR",
                criterion=Criterion.SET_CONSISTENT,
                category=Category.API,
                severity=Severity.CRITICAL,
                hard=False,
                line=no,
                quote=quote,
                message=f"Пример ответа с успешным кодом {code} содержит ошибку в теле — исход запроса не определяется по коду.",
                recommendation="Ошибки валидации возвращать кодом 4xx с телом ошибки; 2xx — только для успеха.",
            )
        )
    if declared:
        for code, no in mentions.items():
            if code not in declared:
                out.append(
                    Signal(
                        rule_id="R-STATUS-UNDECLARED",
                        criterion=Criterion.SET_COMPLETE,
                        category=Category.API,
                        severity=Severity.MAJOR,
                        hard=False,
                        line=no,
                        quote=clean_snippet(doc.lines[no - 1].text),
                        message=f"Код ответа {code} упоминается, но не описан отдельным статусом с примером.",
                        recommendation=f"Описать статус {code}: условие, тело ответа, пример — или убрать упоминание.",
                    )
                )
    return out


def _type_mismatch(doc: SourceDocument) -> list[Signal]:
    types: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for table in iter_tables(doc):
        header = [h.strip() for h in table.header]
        name_col = next((i for i, h in enumerate(header) if NAME_COL_RE.match(h)), None)
        type_col = next((i for i, h in enumerate(header) if TYPE_COL_RE.match(h)), None)
        if name_col is None or type_col is None:
            continue
        for no, cells in table.rows:
            if len(cells) <= max(name_col, type_col):
                continue
            name = cells[name_col].strip().strip("`")
            ftype = cells[type_col].strip().lower()
            if name and ftype:
                types[name].append((ftype, no))
    out: list[Signal] = []
    for name, entries in types.items():
        distinct = {t for t, _ in entries}
        if len(distinct) > 1:
            where = "; ".join(f"{t} (строка {no})" for t, no in entries)
            out.append(
                Signal(
                    rule_id="R-TYPES",
                    criterion=Criterion.SET_CONSISTENT,
                    category=Category.DATA,
                    severity=Severity.MAJOR,
                    hard=False,
                    line=entries[-1][1],
                    quote=clean_snippet(doc.lines[entries[-1][1] - 1].text),
                    message=f"Поле {name} описано с разными типами: {where}.",
                    recommendation=f"Привести тип {name} к одному формату в запросе и ответе.",
                )
            )
    return out
