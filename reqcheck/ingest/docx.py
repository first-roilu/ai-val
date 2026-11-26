"""DOCX → Markdown: заголовки, списки, таблицы в порядке документа."""

from __future__ import annotations

import re
from pathlib import Path

from reqcheck.errors import InputError


def _heading_level(style_name: str) -> int:
    name = style_name.lower()
    if name in ("title", "заголовок", "название"):
        return 1
    m = re.match(r"(heading|заголовок)\s*(\d)", name)
    return int(m.group(2)) if m else 0


def extract_docx(path: Path) -> tuple[list[tuple[str, int | None]], list[str]]:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:  # pragma: no cover
        raise InputError("Для чтения DOCX нужен пакет python-docx: pip install -r requirements.txt") from exc

    try:
        document = docx.Document(str(path))
    except Exception as exc:
        raise InputError(f"Не удалось прочитать DOCX: {exc}") from exc

    out: list[str] = []
    auto_numbering = False
    for block in document.iter_inner_content():
        if isinstance(block, Paragraph):
            text = " ".join(block.text.split())
            if not text:
                out.append("")
                continue
            style = block.style.name if block.style is not None else ""
            level = _heading_level(style or "")
            if level:
                out.extend([f"{'#' * level} {text}", ""])
                continue
            ppr = block._p.pPr
            numbered = ppr is not None and ppr.numPr is not None
            if numbered or "list" in (style or "").lower() or "спис" in (style or "").lower():
                auto_numbering = auto_numbering or numbered
                out.append(f"- {text}")
            else:
                out.append(text)
        elif isinstance(block, Table):
            rows = []
            for row in block.rows:
                rows.append([" ".join(cell.text.split()).replace("|", "\\|") for cell in row.cells])
            if not rows:
                continue
            width = max(len(r) for r in rows)
            rows = [r + [""] * (width - len(r)) for r in rows]
            out.append("")
            out.append("| " + " | ".join(rows[0]) + " |")
            out.append("|" + "---|" * width)
            out.extend("| " + " | ".join(r) + " |" for r in rows[1:])
            out.append("")

    limitations = []
    if auto_numbering:
        limitations.append(
            "DOCX: номера автонумерации Word не извлекаются — пункты автосписков показаны без номеров."
        )
    return [(line, None) for line in out], limitations
