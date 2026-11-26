"""Цикл уточнений: файл ответов, вопросы в консоли, учёт ответов в дефектах и вердикте."""

from __future__ import annotations

from datetime import datetime

import yaml

from reqcheck.answers import ask_in_console, load_answers, resolve_reply, write_template
from reqcheck.config import Settings
from reqcheck.context import RunContext
from reqcheck.grounding import Grounder
from reqcheck.ingest import load_document
from reqcheck.llm.base import LLMResult
from reqcheck.models import Answer, Category, ClarifyOut, Criterion, Finding, Question, Severity
from reqcheck.orchestrator import run
from reqcheck.steps import clarify
from reqcheck.steps.synthesis import compute_verdict
from reqcheck.trace import RunTrace
from tests.conftest import REQUIREMENTS_MD

F2 = "Месяц определяется по дате совершения покупки в Алматы."
F3 = "На карту приходится максимум один такой push за календарный месяц."
F4 = "следующее достижение вызывает push независимо от истории предыдущей настройки"


def test_reply_number_maps_to_option():
    options = ["оставить как есть", "по дате проведения"]
    assert resolve_reply("2", options) == "Вариант 2: по дате проведения"
    assert resolve_reply("по дате авторизации", options) == "по дате авторизации"


def test_answers_file_roundtrip(tmp_path):
    settings = Settings(provider="fake")
    settings.runs_dir, settings.cache_dir = tmp_path / "runs", tmp_path / "cache"
    result = run(settings, REQUIREMENTS_MD, tmp_path / "report.md", notify=lambda m: None)
    report = result.report
    report.questions = [
        Question(qid="Q-01", text="Месяц покупки — по дате совершения или проведения?", addressee="Бизнес",
                 options=["оставить как есть", "по дате проведения"], finding_ids=["D-001"], priority=1),
        Question(qid="Q-02", text="Какой часовой пояс?", addressee="Аналитик", priority=2),
    ]
    path = write_template(report, result.markdown_path, "docs/requirements.md")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("# Ответы на уточняющие вопросы") and "--answers" in text
    data = yaml.safe_load(text)
    data["questions"][0]["answer"] = 2
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    answers, sha = load_answers(path)
    assert sha == report.passport.sha256
    assert len(answers) == 1  # пустой ответ на Q-02 пропущен
    assert answers[0].answer == "Вариант 2: по дате проведения" and answers[0].finding_ids == ["D-001"]


def test_console_questions_skip_and_quit():
    questions = [
        Question(qid="Q-01", text="Вопрос 1?", addressee="Бизнес", options=["да", "нет"], priority=1),
        Question(qid="Q-02", text="Вопрос 2?", addressee="QA", priority=2),
        Question(qid="Q-03", text="Вопрос 3?", addressee="QA", priority=1),
    ]
    replies = iter(["1", "", "q"])
    out: list[str] = []
    answers = ask_in_console(questions, write=out.append, read=lambda prompt: next(replies))
    assert [a.qid for a in answers] == ["Q-01"] and answers[0].answer == "Вариант 1: да"
    assert any("Вопрос 2?" in line for line in out)


class _Scripted:
    name, model = "stub", "stub"

    def __init__(self, data: ClarifyOut):
        self.data = data

    def generate(self, req):
        assert req.step == "clarify" and "<answer>" in req.user
        return LLMResult(raw=self.data.model_dump_json(), data=self.data, model="stub")


def _ctx(tmp_path, out: ClarifyOut) -> RunContext:
    doc = load_document(REQUIREMENTS_MD)
    grounder = Grounder(doc)
    ctx = RunContext(
        settings=Settings(provider="gemini"),
        doc=doc,
        grounder=grounder,
        llm=_Scripted(out),
        trace=RunTrace(tmp_path / "runs", "t", datetime.now()),
        notify=lambda m: None,
        started=datetime.now().astimezone(),
    )

    def finding(fid, severity, criterion, quote):
        return Finding(fid=fid, criterion=criterion, category=Category.TIME, severity=severity, title=fid,
                       problem="…", evidence=[grounder.evidence(quote)])

    ctx.findings = [
        finding("D-001", Severity.CRITICAL, Criterion.SET_CONSISTENT, F3),
        finding("D-002", Severity.MAJOR, Criterion.UNAMBIGUOUS, F2),
    ]
    return ctx


def test_clarify_resolves_findings_and_softens_verdict(tmp_path):
    out = ClarifyOut.model_validate({
        "decisions": [
            {"fid": "D-001", "status": "resolved", "by": ["Q-01"], "note": "Не более одного push в месяц — приоритетнее Ф-4."},
            {"fid": "D-002", "status": "partial", "by": [], "note": "Дата выбрана, пояс не назван."},
        ],
        "amendments": [{"qid": "Q-01", "wording": "Не более одного push за календарный месяц, включая смену порога.", "where": "Ф-4"}],
        "new_findings": [{
            "rids": [], "criterion": "Н-СОГЛ", "category": "процесс", "severity": "MAJOR", "title": "Ф-4 противоречит ответу",
            "quotes": [F4], "problem": "Ф-4 требует push после смены порога, а по ответу — не более одного в месяц.",
            "impact": "", "scenario": "", "recommendation": "Поправить Ф-4.", "question": "",
        }],
    })
    ctx = _ctx(tmp_path, out)
    assert compute_verdict(ctx).development == "no_go"
    answers = [Answer(qid="Q-01", question="Сколько push в месяц?", answer="Вариант 2: не более одного", finding_ids=["D-001", "D-002"])]
    clarify.run(ctx, answers)
    d1, d2 = ctx.findings[0], ctx.findings[1]
    assert d1.resolution == "resolved" and d1.resolved_by == ["Q-01"]
    assert d2.resolution == "partial" and d2.resolved_by == ["Q-01"]  # by пуст — взят из finding_ids вопроса
    assert answers[0].resolved == ["D-001"] and answers[0].partial == ["D-002"]
    assert answers[0].amendment.startswith("Не более одного push") and answers[0].where == "Ф-4"
    assert ctx.findings[-1].fid == "D-003" and answers[0].new_findings == ["D-003"]
    # критичный дефект снят ответом: разработка больше не «нельзя», но и не «можно» без правки документа
    assert compute_verdict(ctx).development == "conditional"


def test_clarify_rerun_starts_clean(tmp_path):
    out = ClarifyOut.model_validate({"decisions": [{"fid": "D-001", "status": "resolved", "by": ["Q-01"], "note": ""}]})
    ctx = _ctx(tmp_path, out)
    clarify.run(ctx, [Answer(qid="Q-01", question="?", answer="да")])
    ctx.llm = _Scripted(ClarifyOut())
    clarify.run(ctx, [Answer(qid="Q-01", question="?", answer="не знаю")])
    assert all(f.resolution == "" for f in ctx.findings)


def test_offline_run_keeps_user_answers_in_file(tmp_path):
    settings = Settings(provider="fake")
    settings.runs_dir, settings.cache_dir = tmp_path / "runs", tmp_path / "cache"
    answers_file = tmp_path / "in.answers.yaml"
    answers_file.write_text(
        yaml.safe_dump({"questions": [{"id": "Q-01", "question": "Часовой пояс?", "options": {1: "UTC+5"}, "answer": 1}]},
                       allow_unicode=True),
        encoding="utf-8",
    )
    result = run(settings, REQUIREMENTS_MD, tmp_path / "report.md", notify=lambda m: None, answers_file=answers_file)
    assert any("не учтены" in x for x in result.report.passport.limitations)
    saved = yaml.safe_load(result.answers_path.read_text(encoding="utf-8"))
    assert any(q.get("answer") == "Вариант 1: UTC+5" for q in saved["questions"])  # ответ пользователя не потерян


def test_open_questions_continue_numbering_after_answers(tmp_path):
    from reqcheck.models import SynthesisOut
    from reqcheck.steps import synthesis

    out = SynthesisOut.model_validate({
        "summary": "…", "rationale_dev": "…", "rationale_test": "…", "fix_first": [],
        "questions": [
            {"text": "Сколько push в месяц?", "addressee": "Бизнес", "why": "", "options": [], "recommended": "",
             "finding_ids": [], "priority": 1},  # уже отвечен — должен уйти
            {"text": "Какой часовой пояс у границы месяца?", "addressee": "Аналитик", "why": "", "options": [],
             "recommended": "", "finding_ids": [], "priority": 2},
        ],
    })

    class _Synth:
        name, model = "stub", "stub"

        def generate(self, req):
            return LLMResult(raw=out.model_dump_json(), data=out, model="stub")

    ctx = _ctx(tmp_path, ClarifyOut())
    ctx.llm = _Synth()
    ctx.answers = [Answer(qid="Q-01", question="Сколько push в месяц?", answer="один"),
                   Answer(qid="Q-04", question="Какой метод API?", answer="POST")]
    synthesis.run(ctx)
    assert [q.qid for q in ctx.questions] == ["Q-05"]
    assert ctx.questions[0].text.startswith("Какой часовой пояс")
