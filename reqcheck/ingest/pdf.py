"""PDF → Markdown по страницам через pymupdf4llm."""

from __future__ import annotations

import os
import re
import sys
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from reqcheck.errors import InputError


@contextmanager
def _quiet_native_output():
    """pymupdf4llm печатает служебные сообщения на уровне C, мимо sys.stdout — глушим дескрипторы 1 и 2."""
    saved: list[tuple[int, int]] = []
    try:
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.flush()
                fd = stream.fileno()
            except (AttributeError, OSError, ValueError):
                continue
            saved.append((fd, os.dup(fd)))
        devnull = os.open(os.devnull, os.O_WRONLY)
        for fd, _ in saved:
            os.dup2(devnull, fd)
        os.close(devnull)
        yield
    finally:
        for fd, copy in saved:
            os.dup2(copy, fd)
            os.close(copy)


def extract_pdf(path: Path) -> tuple[list[tuple[str, int | None]], list[str]]:
    try:
        import pymupdf4llm
    except ImportError as exc:  # pragma: no cover
        raise InputError("Для чтения PDF нужен пакет pymupdf4llm: pip install -r requirements.txt") from exc

    try:
        with _quiet_native_output():
            chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True)
    except Exception as exc:
        raise InputError(f"Не удалось прочитать PDF: {exc}") from exc

    pages: list[tuple[int, list[str]]] = []
    for i, chunk in enumerate(chunks, start=1):
        meta = chunk.get("metadata", {}) if isinstance(chunk, dict) else {}
        page_no = int(meta.get("page_number") or meta.get("page") or i)
        text = chunk.get("text", "") if isinstance(chunk, dict) else str(chunk)
        pages.append((page_no, text.splitlines()))

    # колонтитулы: строки, повторяющиеся больше чем на половине страниц (цифры не различаем)
    repeated: set[str] = set()
    if len(pages) >= 3:
        counter: Counter[str] = Counter()
        for _, lines in pages:
            counter.update({_skeleton(line) for line in lines if line.strip()})
        repeated = {
            key for key, count in counter.items() if count > len(pages) / 2 and len(key) < 80 and not key.startswith("|")
        }

    out: list[tuple[str, int | None]] = []
    for page_no, lines in pages:
        for line in lines:
            if line.strip() and _skeleton(line) in repeated:
                continue
            out.append((line.rstrip(), page_no))

    chars = sum(len(text.strip()) for text, _ in out)
    if chars < 100 * max(1, len(pages)):
        raise InputError(
            "В PDF почти нет текстового слоя (похоже на скан). Распознавание сканов не поддерживается: "
            "пришлите PDF с текстом, DOCX или Markdown."
        )
    return out, []


def _skeleton(line: str) -> str:
    return re.sub(r"\d+", "#", line.strip())
