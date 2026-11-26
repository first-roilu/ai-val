"""Воспроизведение прогона по трассе (--replay) и контрольная сумма документа, не зависящая от ОС."""

import hashlib
import shutil

import pytest

from reqcheck.cli import main
from reqcheck.config import Settings
from reqcheck.ingest import load_document
from reqcheck.llm.replay import ReplayMismatch, compare_with_recorded
from reqcheck.orchestrator import run
from tests.conftest import REQUIREMENTS_MD, ROOT

SUBMITTED_TRACE = ROOT / "runs" / "20261126-202502-docs-requirements"


def _settings(tmp_path, **extra):
    s = Settings(provider="fake", **extra)
    s.runs_dir = tmp_path / "runs"
    s.cache_dir = tmp_path / "cache"
    return s


def test_sha256_does_not_depend_on_line_endings(tmp_path):
    lf = tmp_path / "lf.md"
    crlf = tmp_path / "crlf.md"
    text = "# Требования\n\nФ-1. Порог от 1 000 до 1 000 000 ₸.\n"
    lf.write_bytes(text.encode("utf-8"))
    crlf.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    assert load_document(lf).sha256 == load_document(crlf).sha256 == hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_replay_reproduces_recorded_run(tmp_path):
    doc = tmp_path / "requirements.md"
    shutil.copy(REQUIREMENTS_MD, doc)
    original = run(_settings(tmp_path), doc, tmp_path / "original.md", notify=lambda m: None)
    replayed = run(_settings(tmp_path, replay_dir=original.run_dir), doc, tmp_path / "replay.md", notify=lambda m: None)
    assert [f.fid for f in replayed.report.findings] == [f.fid for f in original.report.findings]
    problems, reference = compare_with_recorded(original.run_dir, replayed.report, replayed.markdown_path)
    assert problems == []
    assert reference.endswith("original.md")
    assert "Воспроизведение прогона" in replayed.markdown_path.read_text(encoding="utf-8")


def test_replay_stops_when_document_changed(tmp_path):
    doc = tmp_path / "requirements.md"
    shutil.copy(REQUIREMENTS_MD, doc)
    original = run(_settings(tmp_path), doc, tmp_path / "original.md", notify=lambda m: None)
    doc.write_text(doc.read_text(encoding="utf-8").replace("1 000 000", "2 000 000"), encoding="utf-8")
    with pytest.raises(ReplayMismatch):
        run(_settings(tmp_path, replay_dir=original.run_dir), doc, tmp_path / "replay.md", notify=lambda m: None)


def _isolate(monkeypatch):
    """CLI без .env разработчика: воспроизведению ключ не нужен, а переменные из .env не должны попасть в другие тесты."""
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr("reqcheck.config.load_dotenv", lambda *args, **kwargs: None)


@pytest.mark.skipif(not SUBMITTED_TRACE.is_dir(), reason="нет трассы сданного прогона")
def test_code_reproduces_submitted_report(tmp_path, monkeypatch):
    """Код в репозитории пересобирает сданный отчёт reports/report.md из записанных ответов модели."""
    _isolate(monkeypatch)
    monkeypatch.setenv("REQCHECK_RUNS_DIR", str(tmp_path / "runs"))
    out = tmp_path / "replay.md"
    code = main(["docs/requirements.md", str(out), "--replay", "runs/20261126-202502-docs-requirements"])
    assert code == 0
    assert "Воспроизведение прогона" in out.read_text(encoding="utf-8")


def test_replay_refuses_to_overwrite_recorded_report(monkeypatch):
    if not SUBMITTED_TRACE.is_dir():
        pytest.skip("нет трассы сданного прогона")
    _isolate(monkeypatch)
    assert main(["docs/requirements.md", "reports/report.md", "--replay", "runs/20261126-202502-docs-requirements"]) == 2
