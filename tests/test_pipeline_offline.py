"""Сквозной прогон без модели (LLM_PROVIDER=fake): конвейер, отчёт и JSON."""

import json

import pytest

from reqcheck.config import Settings
from reqcheck.models import Report
from reqcheck.orchestrator import run
from tests.conftest import REQUIREMENTS_MD, SAMPLES

REQUIRED_SECTIONS = [
    "## 1. Паспорт прогона",
    "## 3. Нарезка требований",
    "## 4. Проверка каждого требования",
    "## 5. Проверка набора требований целиком",
    "## 7. Сводка",
    "## 8. Итог",
]


def _settings(tmp_path):
    s = Settings(provider="fake")
    s.runs_dir = tmp_path / "runs"
    s.cache_dir = tmp_path / "cache"
    return s


def test_offline_run_writes_report_and_json(tmp_path):
    result = run(_settings(tmp_path), REQUIREMENTS_MD, tmp_path / "report.md", notify=lambda m: None)
    text = result.markdown_path.read_text(encoding="utf-8")
    for heading in REQUIRED_SECTIONS:
        assert heading in text
    assert "SHA-256" in text and "Версия агента" in text
    report = Report.model_validate(json.loads(result.json_path.read_text(encoding="utf-8")))
    assert report.stats.requirements_total > 20
    assert any(f.hard for f in report.findings)
    assert all(f.evidence for f in report.findings)
    assert (result.run_dir / "trace.jsonl").is_file()


@pytest.mark.parametrize("name", ["05-bnpl-partners/spec.pdf", "06-sme-online-credit/spec.docx", "09-new-device-alert/spec.md"])
def test_offline_run_on_samples(tmp_path, name):
    path = SAMPLES / name
    if not path.is_file():
        pytest.skip("нет файла корпуса")
    result = run(_settings(tmp_path), path, tmp_path / "r.md", notify=lambda m: None)
    assert result.report.stats.requirements_total > 0
    if name.startswith("09"):
        assert any("R-INJECTION" in " ".join(f.source) for f in result.report.findings)
