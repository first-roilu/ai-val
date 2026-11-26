"""Нормализованное представление документа: пронумерованные строки и дерево разделов."""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass, field

ATX_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
NUMBERED_RE = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,4})\.?\s+(\S.{0,100})$")
BOLD_LINE_RE = re.compile(r"^\s*\*\*([^*]{2,100})\*\*\s*:?\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
PATH_SEP = " › "


@dataclass
class Line:
    no: int
    text: str
    page: int | None
    section_id: int


@dataclass
class Section:
    id: int
    title: str
    level: int
    path: str
    line_start: int
    line_end: int
    parent: int | None


@dataclass
class SourceDocument:
    name: str
    path: str
    sha256: str
    fmt: str
    lines: list[Line]
    sections: list[Section]
    limitations: list[str] = field(default_factory=list)
    encoding: str = ""
    repair: object | None = None  # RepairReport: что восстановлено и что повреждено безвозвратно

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    def numbered(self, start: int = 1, end: int | None = None) -> str:
        end = end or len(self.lines)
        return "\n".join(f"{line.no:04d}| {line.text}" for line in self.lines[start - 1 : end])

    def section(self, section_id: int) -> Section:
        return self.sections[section_id]

    def section_path_of(self, line_no: int) -> str:
        if not self.lines:
            return ""
        line = self.lines[min(max(line_no, 1), len(self.lines)) - 1]
        return self.sections[line.section_id].path

    def page_of(self, line_no: int) -> int | None:
        if not self.lines:
            return None
        return self.lines[min(max(line_no, 1), len(self.lines)) - 1].page

    def section_lines(self, section: Section, own_only: bool = False) -> list[Line]:
        lines = self.lines[section.line_start - 1 : section.line_end]
        if own_only:
            lines = [line for line in lines if line.section_id == section.id]
        return lines

    def children(self, section: Section) -> list[Section]:
        return [s for s in self.sections if s.parent == section.id]

    def outline(self) -> str:
        """Оглавление для промптов: уровень, название, строки."""
        rows = []
        for s in self.sections[1:]:
            rows.append(f"{'  ' * max(s.level - 1, 0)}- {s.title} (строки {s.line_start}–{s.line_end})")
        return "\n".join(rows)


def clean_title(title: str) -> str:
    title = title.replace("\u00ad", "-")
    title = re.sub(r"[*_`]", "", title).strip()
    return title.rstrip(":").strip()


def _detect_heading(text: str, use_atx: bool) -> tuple[int, str] | None:
    if use_atx:
        m = ATX_RE.match(text)
        if m:
            title = clean_title(m.group(2))
            return (len(m.group(1)), title) if title else None
        return None
    if text.lstrip().startswith("|"):
        return None
    m = ATX_RE.match(text)
    if m:
        return len(m.group(1)), clean_title(m.group(2))
    m = NUMBERED_RE.match(text)
    if m:
        title = m.group(2).strip()
        plain = clean_title(title)
        if plain and plain[0].isupper() and not re.search(r"[.;,!?]$", title) and len(plain) <= 100:
            return m.group(1).count(".") + 1, f"{m.group(1)} {plain}"
    m = BOLD_LINE_RE.match(text)
    if m:
        return 6, clean_title(m.group(1))
    return None


def build_document(
    *,
    name: str,
    path: str,
    sha256: str,
    fmt: str,
    raw_lines: list[tuple[str, int | None]],
    limitations: list[str] | None = None,
) -> SourceDocument:
    texts = [text.rstrip("\r") for text, _ in raw_lines]

    in_fence = False
    atx_count = 0
    for text in texts:
        if FENCE_RE.match(text):
            in_fence = not in_fence
            continue
        if not in_fence and ATX_RE.match(text):
            atx_count += 1
    use_atx = atx_count >= 2

    sections: list[Section] = [Section(0, "Начало документа", 0, "Начало документа", 1, len(texts), None)]
    raw_heads: list[tuple[int, int, str]] = []  # (line_no, level, title)
    in_fence = False
    for no, text in enumerate(texts, start=1):
        if FENCE_RE.match(text):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        head = _detect_heading(text, use_atx)
        if head:
            raw_heads.append((no, head[0], head[1]))

    # единственный заголовок первого уровня в начале — это название документа, в путь его не включаем
    level1 = [h for h in raw_heads if h[1] == 1]
    title_line = level1[0][0] if len(level1) == 1 and level1[0] == raw_heads[0] else None

    stack: list[Section] = [sections[0]]
    heading_at: dict[int, int] = {}
    for no, level, title in raw_heads:
        while len(stack) > 1 and stack[-1].level >= level:
            stack.pop()
        parent = stack[-1]
        sid = len(sections)
        if no == title_line:
            section_path = "Начало документа"
        else:
            chain = [s.title for s in stack[1:] if s.line_start != title_line] + [title]
            section_path = PATH_SEP.join(_short(t) for t in chain)
        section = Section(sid, title, level, section_path, no, len(texts), parent.id)
        sections.append(section)
        stack.append(section)
        heading_at[no] = sid

    # границы разделов: до следующего заголовка того же или более высокого уровня
    for i, section in enumerate(sections[1:], start=1):
        for nxt in sections[i + 1 :]:
            if nxt.level <= section.level:
                section.line_end = nxt.line_start - 1
                break

    lines: list[Line] = []
    current = 0
    starts = [s.line_start for s in sections[1:]]
    for no, (text, (_, page)) in enumerate(zip(texts, raw_lines), start=1):
        if no in heading_at:
            current = heading_at[no]
        elif starts:
            idx = bisect_right(starts, no)
            current = idx if idx > 0 else 0
            # bisect даёт последний раздел, начавшийся до строки; это и есть самый глубокий раздел
        lines.append(Line(no, text, page, current))

    return SourceDocument(
        name=name,
        path=path,
        sha256=sha256,
        fmt=fmt,
        lines=lines,
        sections=sections,
        limitations=list(limitations or []),
    )


def _short(title: str, limit: int = 60) -> str:
    return title if len(title) <= limit else title[: limit - 1].rstrip() + "…"
