"""Общие помощники для правил."""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from reqcheck.grounding import clean_snippet
from reqcheck.ingest.structure import FENCE_RE, Line, SourceDocument


def iter_text_lines(doc: SourceDocument, include_code: bool = False) -> Iterator[tuple[Line, bool]]:
    """Строки документа с признаком «внутри блока кода»."""
    in_code = False
    for line in doc.lines:
        if FENCE_RE.match(line.text):
            in_code = not in_code
            continue
        if in_code and not include_code:
            continue
        yield line, in_code


def is_heading(text: str) -> bool:
    return bool(re.match(r"^\s{0,3}#{1,6}\s", text))


def split_row(text: str) -> list[str]:
    inner = text.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def is_separator_row(text: str) -> bool:
    return bool(re.match(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$", text))


def plain(cell: str) -> str:
    return clean_snippet(cell)


@dataclass
class Table:
    header: list[str]
    header_line: int
    rows: list[tuple[int, list[str]]]  # (номер строки, ячейки)
    section_id: int


def iter_tables(doc: SourceDocument) -> Iterator[Table]:
    lines = doc.lines
    i = 0
    while i < len(lines) - 1:
        text = lines[i].text
        if text.strip().startswith("|") and is_separator_row(lines[i + 1].text):
            header = [plain(c) for c in split_row(text)]
            rows = []
            j = i + 2
            while j < len(lines) and lines[j].text.strip().startswith("|"):
                rows.append((lines[j].no, [plain(c) for c in split_row(lines[j].text)]))
                j += 1
            yield Table(header, lines[i].no, rows, lines[i].section_id)
            i = j
        else:
            i += 1
