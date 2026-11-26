import pytest

from reqcheck.errors import InputError
from reqcheck.ingest import load_document
from tests.conftest import SAMPLES


def test_markdown_sections(requirements_doc):
    doc = requirements_doc
    assert doc.fmt == "md" and len(doc.lines) > 150
    paths = [s.path for s in doc.sections]
    assert "Часть A. Новый функционал › Требования к изменениям › Функциональные требования" in paths
    assert doc.section_path_of(39).endswith("Функциональные требования")
    assert doc.path.endswith("requirements.md")  # путь к файлу, а не путь раздела


def test_outline_and_numbering(requirements_doc):
    assert requirements_doc.numbered(1, 2).startswith("0001| # ")
    assert "Функциональные требования" in requirements_doc.outline()


@pytest.mark.skipif(not (SAMPLES / "05-bnpl-partners" / "spec.pdf").is_file(), reason="нет PDF в корпусе")
def test_pdf_has_pages_and_sections():
    doc = load_document(SAMPLES / "05-bnpl-partners" / "spec.pdf")
    assert doc.fmt == "pdf"
    assert {line.page for line in doc.lines if line.page} >= {1, 2}
    assert len(doc.sections) > 5


@pytest.mark.skipif(not (SAMPLES / "06-sme-online-credit" / "spec.docx").is_file(), reason="нет DOCX в корпусе")
def test_docx_headings_and_tables():
    doc = load_document(SAMPLES / "06-sme-online-credit" / "spec.docx")
    assert doc.fmt == "docx"
    assert len(doc.sections) > 5
    assert any(line.text.startswith("|") for line in doc.lines)


def test_missing_and_unsupported(tmp_path):
    with pytest.raises(InputError):
        load_document(tmp_path / "nope.md")
    bad = tmp_path / "x.doc"
    bad.write_bytes(b"\xd0\xcf\x11\xe0 old word")
    with pytest.raises(InputError):
        load_document(bad)
