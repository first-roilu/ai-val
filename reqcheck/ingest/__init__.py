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
MAX_DOCX_UNPACKED = 200 * 1024 * 1024  # больше после распаковки — повреждённый архив или zip-бомба
MAX_CONTROL_SHARE = 0.02  # доля управляющих символов C0, выше которой «текст» на деле двоичный файл

_IMAGE = "Распознавание изображений не поддерживается: пришлите PDF с текстовым слоем, DOCX, MD или TXT."
_NOT_DOCUMENT = "Поддерживаются PDF, DOCX, MD и TXT."
# Двоичные форматы, которые агент не читает: начало файла, что это, что делать
_SIGNATURES: list[tuple[bytes, str, str]] = [
    (b"\x89PNG\r\n\x1a\n", "изображение PNG", _IMAGE),
    (b"\xff\xd8\xff", "изображение JPEG", _IMAGE),
    (b"GIF87a", "изображение GIF", _IMAGE),
    (b"GIF89a", "изображение GIF", _IMAGE),
    (b"II*\x00", "изображение TIFF", _IMAGE),
    (b"MM\x00*", "изображение TIFF", _IMAGE),
    (
        b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",
        "документ Office 97–2003 (DOC, XLS) или документ Office, защищённый паролем",
        "Сохраните его как DOCX без пароля или как PDF.",
    ),
    (b"{\\rtf", "документ RTF", "Сохраните его как DOCX или PDF."),
    (b"Rar!\x1a\x07", "архив RAR", _NOT_DOCUMENT),
    (b"7z\xbc\xaf\x27\x1c", "архив 7z", _NOT_DOCUMENT),
    (b"\x1f\x8b", "архив gzip", _NOT_DOCUMENT),
    (b"\x7fELF", "исполняемый файл", _NOT_DOCUMENT),
]


def _refuse_binary(path: Path, data: bytes) -> None:
    """Картинки, архивы и старые форматы Office отклоняются до чтения: иначе их байты ушли бы в отчёт как «текст»."""
    for signature, kind, advice in _SIGNATURES:
        if data.startswith(signature):
            raise InputError(f"{path.name}: это {kind}, а не документ с текстом. {advice}")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        raise InputError(f"{path.name}: это изображение WEBP, а не документ с текстом. {_IMAGE}")


def control_share(text: str) -> float:
    """Доля управляющих символов C0 и DEL, кроме пробельных. У текста — почти ноль, у двоичного файла,
    прочитанного в однобайтовой кодировке, — около 10 %. Символы C1 (U+0080–U+009F) не считаются:
    они бывают в тексте с двойной перекодировкой, а его чинит восстановление."""
    sample = text[:200_000]
    if not sample:
        return 0.0
    bad = sum(1 for ch in sample if (ch < " " and ch not in "\t\n\r\f\v") or ch == "\x7f")
    return bad / len(sample)


def _detect_format(path: Path, data: bytes) -> str:
    if data[:4] == b"%PDF":
        return "pdf"
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(BytesIO(data)) as zf:
                names = set(zf.namelist())
                unpacked = sum(info.file_size for info in zf.infolist())
        except zipfile.BadZipFile:
            names, unpacked = set(), 0
        if "word/document.xml" not in names:
            raise InputError(f"{path.name}: архив не похож на DOCX. Поддерживаются PDF, DOCX, MD, TXT.")
        if unpacked > MAX_DOCX_UNPACKED:
            raise InputError(
                f"{path.name}: после распаковки DOCX занимает {unpacked // 2**20} МБ — файл повреждён "
                "или это zip-бомба. Пересохраните документ в Word."
            )
        return "docx"
    _refuse_binary(path, data)
    ext = path.suffix.lower()
    if ext in (".md", ".markdown"):
        return "md"
    if ext in TEXT_EXTENSIONS or ext in (".rst", ".csv"):
        return "txt"
    if ext in (".doc", ".rtf", ".odt", ".pptx", ".xlsx"):
        raise InputError(f"Формат {ext} не поддерживается. Сохраните документ как PDF, DOCX или Markdown.")
    return "txt"


def document_sha256(data: bytes, fmt: str) -> str:
    """Контрольная сумма документа. У текстовых файлов переводы строк CRLF приводятся к LF:
    Git на Windows (core.autocrlf) отдаёт тот же файл с CRLF, а сумма не должна зависеть от ОС."""
    if fmt in ("md", "txt"):
        data = data.replace(b"\r\n", b"\n")
    return hashlib.sha256(data).hexdigest()


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
        share = control_share(text)
        if share > MAX_CONTROL_SHARE:
            raise InputError(
                f"{p.name}: файл не похож на текст — это двоичные данные (управляющих символов {share:.0%}). "
                "Поддерживаются PDF, DOCX, MD и TXT в UTF-8, UTF-16 или cp1251."
            )
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        raw_lines = [(line, None) for line in text.split("\n")]
        while raw_lines and not raw_lines[-1][0].strip():
            raw_lines.pop()
        if encoding not in ("utf-8", "utf-16"):
            limitations.append(f"Файл не в UTF-8: кодировка определена как {encoding}.")
    if not any(text.strip() for text, _ in raw_lines):
        raise InputError(f"В документе не найден текст: {p}")
    ext = p.suffix.lower()
    if ext in (".pdf", ".docx") and fmt != ext[1:]:
        limitations.append(f"Расширение {ext} не совпадает с содержимым: файл прочитан как {fmt.upper()}.")

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
        sha256=document_sha256(data, fmt),
        fmt=fmt,
        raw_lines=raw_lines,
        limitations=limitations,
    )
    doc.encoding = encoding or ("utf-8" if fmt in ("md", "txt") else "")
    doc.repair = report
    return doc
