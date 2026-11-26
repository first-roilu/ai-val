"""Критик: итог не зависит от порядка решений; понижение критичности не отменяется слиянием дубля;
причины отклонения ссылаются на итоговые номера находок."""

from __future__ import annotations

from datetime import datetime

from reqcheck.config import Settings
from reqcheck.context import RunContext
from reqcheck.grounding import Grounder
from reqcheck.ingest import load_document
from reqcheck.llm.base import LLMResult
from reqcheck.models import Category, CriticOut, Criterion, Finding, Severity
from reqcheck.steps import critic
from reqcheck.trace import RunTrace
from tests.conftest import REQUIREMENTS_MD

F3 = "На карту приходится максимум один такой push за календарный месяц."


class _Scripted:
    def __init__(self, data: CriticOut):
        self.data = data

    def generate(self, req):
        assert req.step == "critic"
        return LLMResult(raw=self.data.model_dump_json(), data=self.data, model="stub")


def _run(tmp_path, specs: list[tuple[str, Severity, str]], decisions: list[dict]) -> RunContext:
    doc = load_document(REQUIREMENTS_MD)
    grounder = Grounder(doc)
    out = CriticOut.model_validate({"decisions": decisions, "missed": []})
    ctx = RunContext(
        settings=Settings(provider="gemini"),
        doc=doc,
        grounder=grounder,
        llm=_Scripted(out),
        trace=RunTrace(tmp_path / "runs", "t", datetime.now()),
        notify=lambda m: None,
        started=datetime.now().astimezone(),
    )
    ctx.findings = [
        Finding(fid=fid, criterion=Criterion.SET_CONSISTENT, category=Category.PROCESS, severity=severity,
                title=title, problem=title, evidence=[grounder.evidence(F3)])
        for fid, severity, title in specs
    ]
    critic.run(ctx)
    return ctx


def _decision(fid, action, target="", severity="CRITICAL", reason="") -> dict:
    return {"fid": fid, "action": action, "target": target, "new_severity": severity, "reason": reason, "evidence_quote": ""}


def test_downgrade_survives_merge_of_critical_duplicate_in_any_order(tmp_path):
    specs = [("D-001", Severity.CRITICAL, "Ключ идемпотентности при трёх попытках"),
             ("D-002", Severity.CRITICAL, "Повтор попытки с тем же ключом")]
    downgrade = _decision("D-001", "severity", severity="MAJOR", reason="документ снимает")
    merge = _decision("D-002", "merge", target="D-001", reason="дубль")
    for decisions in ([downgrade, merge], [merge, downgrade]):
        findings = _run(tmp_path, specs, decisions).findings
        assert [(f.title, f.severity) for f in findings] == [("Ключ идемпотентности при трёх попытках", Severity.MAJOR)]


def test_rejected_reasons_use_final_ids(tmp_path):
    specs = [("D-001", Severity.MINOR, "Мелочь"), ("D-002", Severity.CRITICAL, "Главное"), ("D-003", Severity.CRITICAL, "Дубль главного")]
    ctx = _run(tmp_path, specs, [_decision("D-003", "merge", target="D-002", reason="то же, что D-002")])
    assert [(f.fid, f.title) for f in ctx.findings] == [("D-001", "Главное"), ("D-002", "Мелочь")]
    assert ctx.rejected[-1]["reason"] == "слита с D-001: то же, что D-001"
