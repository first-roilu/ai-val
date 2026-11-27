"""Битые и чужие файлы: понятная ошибка до вызова модели или честный отчёт, но не мусор в отчёте."""

import json
import zipfile

import pytest

import reqcheck.ingest as ingest
from reqcheck.config import Settings
from reqcheck.errors import InputError
from reqcheck.ingest import load_document
from reqcheck.models import Report
from reqcheck.orchestrator import run


def test_images_archives_and_old_office_are_refused(tmp_path):
    cases = {
        "scan.pdf": (b"\x89PNG\r\n\x1a\n" + bytes(300), "изображение PNG"),
        "photo.md": (b"\xff\xd8\xff\xe0" + bytes(300), "изображение JPEG"),
        "locked.docx": (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + bytes(300), "паролем"),
        "spec.txt": (b"{\\rtf1\\ansi " + b"x" * 300, "RTF"),
    }
    for name, (data, expected) in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        with pytest.raises(InputError, match=expected):
            load_document(path)


def test_binary_garbage_is_not_read_as_text(tmp_path):
    path = tmp_path / "spec.md"
    path.write_bytes(bytes(range(256)) * 40)  # в однобайтовой кодировке «читается», но это не текст
    with pytest.raises(InputError, match="двоичные данные"):
        load_document(path)


def test_text_survives_the_binary_check(tmp_path):
    path = tmp_path / "spec.md"
    path.write_text("# ТЗ\n\nСистема должна сохранять порог.\f\n\tСтрока с табуляцией.\n", encoding="cp1251")
    doc = load_document(path)
    assert any("Система должна сохранять порог" in line.text for line in doc.lines)


def test_utf16_without_bom(tmp_path):
    text = "# ТЗ\n\nСистема должна сохранять порог 50 000 ₸.\n"
    for encoding in ("utf-16-le", "utf-16-be"):
        path = tmp_path / f"{encoding}.md"
        path.write_bytes(text.encode(encoding))
        doc = load_document(path)
        assert any("Система должна сохранять порог 50 000 ₸." in line.text for line in doc.lines)
        assert not any("кодировка" in x for x in doc.limitations)


def test_text_with_pdf_extension_is_read_with_a_note(tmp_path):
    path = tmp_path / "spec.pdf"
    path.write_text("# ТЗ\n\nСистема должна сохранять порог уведомления.\n", encoding="utf-8")
    doc = load_document(path)
    assert doc.fmt == "txt"
    assert any("не совпадает с содержимым" in x for x in doc.limitations)


def test_password_protected_pdf_is_explained(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    pdf = pymupdf.open()
    pdf.new_page().insert_text((72, 72), "Requirement: the system stores the threshold.")
    path = tmp_path / "locked.pdf"
    path.write_bytes(pdf.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner"))
    with pytest.raises(InputError, match="паролем"):
        load_document(path)


def test_docx_bomb_is_refused(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "MAX_DOCX_UNPACKED", 1000)
    path = tmp_path / "big.docx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("word/document.xml", "<w:document/>" + " " * 5000)
    with pytest.raises(InputError, match="zip-бомба"):
        load_document(path)


def test_document_without_requirements_gets_no_go(tmp_path):
    settings = Settings(provider="fake")
    settings.runs_dir = tmp_path / "runs"
    settings.cache_dir = tmp_path / "cache"
    doc = tmp_path / "template.md"
    doc.write_text("# ТЗ\n\n## Цель\n\n## Требования\n\n## Критерии приёмки\n", encoding="utf-8")
    result = run(settings, doc, tmp_path / "report.md", notify=lambda m: None)
    report = Report.model_validate(json.loads(result.json_path.read_text(encoding="utf-8")))
    assert report.stats.requirements_total == 0
    assert (report.verdict.development, report.verdict.testing) == ("no_go", "no_go")
    assert "не найдено ни одного требования" in result.markdown_path.read_text(encoding="utf-8")
    assert not report.questions
