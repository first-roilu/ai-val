"""Загрузка документа любого поддерживаемого формата в SourceDocument."""

from __future__ import annotations

import hashlib
import zipfile
from io import BytesIO
from pathlib import Path

from reqcheck.errors import InputError
from reqcheck.ingest.repair import FIX_LABEL, decode_bytes, repair_lines
from reqcheck.ingest.structure import Line, Section, SourceDocument, build_document

__all__ = ["load_document", "SourceDocument", "Line", "Section"]

TEXT_EXTENSIONS = {".md", ".markdown", ".txt", ".text", ""}


def _detect_format(path: Path, data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                if "word/document.xml" in zf.namelist():
                    return "docx"
        except zipfile.BadZipFile:
            pass
        raise InputError(f"{path.name}: архив не похож на DOCX. Поддерживаются PDF, DOCX, MD, TXT.")
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return "md"
    if ext in TEXT_EXTENSIONS or ext in (".rst", ".csv"):
        return "txt"
    if ext in (".doc", ".rtf", ".odt", ".pptx", ".xlsx"):
        raise InputError(f"Формат {ext} не поддерживается. Сохраните документ как PDF, DOCX или Markdown.")
    return "txt"


def load_document(path: str | Path) -> SourceDocument:
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Файл не найден: {p}")
    data = p.read_bytes()
    if not data.strip():
        raise InputError(f"Файл пуст: {p}")
    fmt = _detect_format(p, data)
    limitations: list[str] = []
    encoding = ""
    if fmt == "pdf":
        from reqcheck.ingest.pdf import extract_pdf

        raw_lines, limitations = extract_pdf(p)
    elif fmt == "docx":
        from reqcheck.ingest.docx import extract_docx

        raw_lines, limitations = extract_docx(p)
    else:
        text, encoding = decode_bytes(data)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = [(line, None) for line in text.split("\n")]
        while raw_lines and not raw_lines[-1][0].strip():
            raw_lines.pop()
        if encoding not in ("utf-8", "utf-16"):
            limitations.append(f"Файл не в UTF-8: кодировка определена как {encoding}.")
    if not any(text.strip() for text, _ in raw_lines):
        raise InputError(f"В документе не найден текст: {p}")

    raw_lines, report = repair_lines(raw_lines, pdf=fmt == "pdf")
    if report.fixed_lines:
        kinds = "; ".join(f"{FIX_LABEL[k]} — строк {len(set(v))}" for k, v in report.fixed.items())
        limitations.append(f"Текст восстановлен автоматически: {kinds}. Восстановленные места стоит сверить с оригиналом.")
    if report.broken:
        lines = sorted({no for no, _, _ in report.broken})
        shown = ", ".join(map(str, lines[:15])) + (" …" if len(lines) > 15 else "")
        limitations.append(f"Повреждённые фрагменты без возможности восстановления — строки {shown}. Смысл этих мест модель не додумывает.")

    doc = build_document(
        name=p.name,
        path=str(p),
        sha256=hashlib.sha256(data).hexdigest(),
        fmt=fmt,
        raw_lines=raw_lines,
        limitations=limitations,
    )
    doc.encoding = encoding or ("utf-8" if fmt in ("md", "txt") else "")
    doc.repair = report
    return doc
