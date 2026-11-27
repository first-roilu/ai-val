"""Отчёт ложится ровно по указанному пути; форматы, которые агент не создаёт, отклоняются до вызова модели."""

import pytest

from reqcheck.cli import main
from reqcheck.errors import ConfigError
from reqcheck.report.render import report_paths
from tests.conftest import ROOT


def test_report_paths(tmp_path):
    assert report_paths(tmp_path / "r.md") == (tmp_path / "r.md", tmp_path / "r.json")
    assert report_paths(tmp_path / "r.txt") == (tmp_path / "r.txt", tmp_path / "r.json")
    assert report_paths(tmp_path / "r.json") == (tmp_path / "r.md", tmp_path / "r.json")
    assert report_paths(tmp_path, "docs/requirements.md")[0] == tmp_path / "requirements-report.md"
    for name in ("r.pdf", "r.docx", "r.html"):
        with pytest.raises(ConfigError):
            report_paths(tmp_path / name)


def test_report_path_equal_to_document_is_refused(tmp_path):
    doc = tmp_path / "spec.md"
    doc.write_text("# ТЗ\n", encoding="utf-8")
    for out in (doc, doc.with_suffix(".json")):  # report.json пишет Markdown в report.md
        with pytest.raises(ConfigError):
            report_paths(out, doc)


def _isolate(monkeypatch, tmp_path):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr("reqcheck.config.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("REQCHECK_RUNS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("REQCHECK_CACHE_DIR", str(tmp_path / "cache"))


def test_txt_report_is_written_where_asked(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    out = tmp_path / "out" / "report.txt"
    assert main(["docs/requirements.md", str(out), "--provider", "fake"]) == 0
    assert out.read_text(encoding="utf-8").startswith("# Отчёт о статической проверке требований")
    assert (out.parent / "report.json").is_file()
    assert not (out.parent / "report.md").exists()


def test_pdf_report_is_refused_before_the_run(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    out = tmp_path / "out" / "report.pdf"
    assert main(["docs/requirements.md", str(out), "--provider", "fake"]) == 2
    assert not out.parent.exists() and not (tmp_path / "runs").exists()
