"""Шаг 8: статистика и вердикт (код), резюме и уточняющие вопросы (LLM, с запасным вариантом)."""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz

from reqcheck.context import RunContext
from reqcheck.errors import LLMError
from reqcheck.models import (
    CRITERION_NAME,
    SEVERITY_RANK,
    Criterion,
    Finding,
    FixItem,
    PropertyAssessment,
    Question,
    ReqKind,
    Severity,
    Stats,
    SynthesisOut,
    Verdict,
)
from reqcheck.steps.common import ask
from reqcheck.steps.lenses import SET_PROPERTY_ORDER

GO_LABEL = {"go": "можно", "conditional": "условно, после уточнений", "no_go": "нельзя"}
UNVERIFIABLE = {Criterion.VERIFIABLE, Criterion.SET_VALIDATABLE}
CONTRADICTORY = {Criterion.SET_CONSISTENT}
INCOMPLETE = {Criterion.COMPLETE, Criterion.SET_COMPLETE}
CLASS_RANK = {Criterion.SET_CONSISTENT: 0, Criterion.VERIFIABLE: 1, Criterion.SET_VALIDATABLE: 1, Criterion.COMPLETE: 2, Criterion.SET_COMPLETE: 2, Criterion.FEASIBLE: 3, Criterion.SET_FEASIBLE: 3, Criterion.CORRECT: 3}


def compute_stats(ctx: RunContext) -> Stats:
    findings = ctx.findings
    with_findings = {r for f in findings for r in f.card_rids}
    unreviewed = sum(1 for a in ctx.assessments.values() if not a.reviewed)
    total = len(ctx.requirements)
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    return Stats(
        requirements_total=total,
        requirements_with_findings=len(with_findings & set(ctx.req_index)),
        requirements_clean=total - len(with_findings & set(ctx.req_index)),
        requirements_unreviewed=unreviewed,
        findings_total=len(findings),
        by_severity={s.value: sum(1 for f in findings if f.severity == s) for s in Severity},
        by_criterion=dict(Counter(f.criterion.value for f in findings).most_common()),
        by_category=dict(Counter(f.category.value for f in findings).most_common()),
        critical_unverifiable=[f.fid for f in crit if f.criterion in UNVERIFIABLE],
        critical_contradictory=[f.fid for f in crit if f.criterion in CONTRADICTORY],
        critical_incomplete=[f.fid for f in crit if f.criterion in INCOMPLETE],
        critical_other=[f.fid for f in crit if f.criterion not in UNVERIFIABLE | CONTRADICTORY | INCOMPLETE],
        resolved_by_answers=sum(1 for f in findings if f.resolution == "resolved"),
        partial_by_answers=sum(1 for f in findings if f.resolution == "partial"),
    )


def open_findings(findings: list[Finding]) -> list[Finding]:
    """Дефекты, которые ещё блокируют: снятые ответом заказчика (resolved) не блокируют,
    но документ по ним всё равно нужно поправить."""
    return [f for f in findings if f.resolution != "resolved"]


NO_REQUIREMENTS = (
    "В документе не найдено ни одного требования, поэтому проверять нечего. "
    "Нужен документ с функциональными требованиями и критериями приёмки."
)


def compute_verdict(ctx: RunContext) -> Verdict:
    findings = open_findings(ctx.findings)
    has_critical = any(f.severity == Severity.CRITICAL for f in findings)
    has_major = any(f.severity == Severity.MAJOR for f in findings)
    development = "no_go" if has_critical else ("conditional" if has_major else "go")

    no_acceptance = not any(r.kind == ReqKind.ACCEPTANCE for r in ctx.requirements)
    empty_testing = any(
        "R-EMPTY-SECTION" in " ".join(f.source) and re.search(r"тест", f.title + f.problem, re.IGNORECASE) for f in findings
    )
    test_blockers = any(
        f.severity == Severity.CRITICAL and f.criterion in (UNVERIFIABLE | CONTRADICTORY) for f in findings
    )
    test_issues = any(
        f.severity in (Severity.CRITICAL, Severity.MAJOR)
        and f.criterion in {Criterion.VERIFIABLE, Criterion.UNAMBIGUOUS, Criterion.SET_VALIDATABLE, Criterion.SET_TRACEABLE}
        for f in findings
    )
    if test_blockers or no_acceptance or empty_testing:
        testing = "no_go"
    elif test_issues or has_critical or has_major:
        testing = "conditional"
    else:
        testing = "go"
    if len(findings) < len(ctx.findings):
        # решения по ответам приняты, но в документ ещё не внесены
        development = "conditional" if development == "go" else development
        testing = "conditional" if testing == "go" else testing
    if ctx.offline:
        # без смысловой проверки «можно» не выдаём
        development = "conditional" if development == "go" else development
        testing = "conditional" if testing == "go" else testing
    return Verdict(development=development, testing=testing, fix_first=default_fix_first(findings))


def default_fix_first(findings: list[Finding], limit: int = 7) -> list[FixItem]:
    important = [f for f in open_findings(findings) if f.severity != Severity.MINOR]
    important.sort(
        key=lambda f: (
            SEVERITY_RANK[f.severity],
            CLASS_RANK.get(f.criterion, 4),
            -len(f.rids),
            f.evidence[0].locator.line_start if f.evidence else 0,
        )
    )
    return [FixItem(fid=f.fid, action=f.recommendation or f.title) for f in important[:limit]]


def set_properties(ctx: RunContext) -> list[PropertyAssessment]:
    result = []
    for criterion in SET_PROPERTY_ORDER:
        related = [f for f in ctx.findings if f.criterion == criterion]
        draft = ctx.properties.get(criterion.value)
        status = draft.status if draft else "na"
        if any(f.severity in (Severity.CRITICAL, Severity.MAJOR) for f in related):
            status = "fail"
        elif related and status in ("ok", "na"):
            status = "warn"
        summary = draft.summary.strip() if draft else ""
        if not summary and related:
            summary = f"Найдено дефектов: {len(related)}."
        result.append(PropertyAssessment(criterion=criterion, status=status, summary=summary, finding_ids=[f.fid for f in related]))
    return result


def _stats_text(stats: Stats) -> str:
    s = stats.by_severity
    return (
        f"Требований: {stats.requirements_total}, с дефектами: {stats.requirements_with_findings}. "
        f"Дефектов: {stats.findings_total} (CRITICAL {s.get('CRITICAL', 0)}, MAJOR {s.get('MAJOR', 0)}, MINOR {s.get('MINOR', 0)})."
    )


def _findings_listing(findings: list[Finding]) -> str:
    rows = []
    minor = 0
    for f in findings:
        if f.severity == Severity.MINOR:
            minor += 1
            if minor > 15:
                continue
        mark = {"resolved": " [снят ответом заказчика]", "partial": " [частично снят ответом]"}.get(f.resolution, "")
        rows.append(f"- {f.fid} · {f.severity.value} · {f.criterion.value} ({CRITERION_NAME[f.criterion]}){mark} · {f.title} — {f.problem[:260]}")
    return "\n".join(rows) or "(дефектов нет)"


def _answered_listing(ctx: RunContext) -> str:
    if not ctx.answers:
        return ""
    rows = []
    for a in ctx.answers:
        effect = []
        if a.resolved:
            effect.append("сняты " + ", ".join(a.resolved))
        if a.partial:
            effect.append("частично " + ", ".join(a.partial))
        if a.new_findings:
            effect.append("новые " + ", ".join(a.new_findings))
        rows.append(f"- {a.qid} «{a.question}» → {a.answer}" + (f" ({'; '.join(effect)})" if effect else ""))
    return "\n".join(rows)


def _questions_listing(findings: list[Finding]) -> str:
    rows = [f"- [{f.fid}] {q}" for f in findings for q in f.questions]
    return "\n".join(rows[:80]) or "(вопросов не собрано)"


def fallback_questions(findings: list[Finding], limit: int = 15) -> list[Question]:
    picked: list[tuple[str, Finding]] = []
    for f in sorted(findings, key=lambda f: SEVERITY_RANK[f.severity]):
        for q in f.questions:
            if all(fuzz.ratio(q, other) < 85 for other, _ in picked):
                picked.append((q, f))
    return [
        Question(
            qid=f"Q-{i:02d}",
            text=q,
            addressee="Аналитик",
            why=f"Снимает дефект {f.fid}: {f.title}",
            finding_ids=[f.fid],
            priority=1 if f.severity == Severity.CRITICAL else (2 if f.severity == Severity.MAJOR else 3),
        )
        for i, (q, f) in enumerate(picked[:limit], start=1)
    ]


def run_without_requirements(ctx: RunContext) -> None:
    """Требований не найдено: модели нечего резюмировать, итог «нельзя» задаёт код."""
    ctx.stats = compute_stats(ctx)
    ctx.verdict = Verdict(
        development="no_go",
        testing="no_go",
        rationale_dev=NO_REQUIREMENTS,
        rationale_test=NO_REQUIREMENTS,
        fix_first=default_fix_first(ctx.findings),
    )
    ctx.summary = NO_REQUIREMENTS
    ctx.questions = []


def run(ctx: RunContext) -> None:
    ctx.stats = compute_stats(ctx)
    ctx.verdict = compute_verdict(ctx)
    valid = {f.fid for f in ctx.findings}
    try:
        out: SynthesisOut = ask(
            ctx,
            "synthesis",
            "synthesis.md.j2",
            SynthesisOut,
            dev=GO_LABEL[ctx.verdict.development],
            test=GO_LABEL[ctx.verdict.testing],
            stats=_stats_text(ctx.stats),
            findings=_findings_listing(ctx.findings),
            questions=_questions_listing(open_findings(ctx.findings)),
            answered=_answered_listing(ctx),
        )
    except LLMError as exc:
        ctx.limit(f"Резюме и вопросы собраны без модели: {exc}")
        out = None

    if out is not None:
        ctx.summary = out.summary.strip()
        ctx.verdict.rationale_dev = out.rationale_dev.strip()
        ctx.verdict.rationale_test = out.rationale_test.strip()
        fix = [FixItem(fid=x.fid, action=x.action.strip()) for x in out.fix_first if x.fid in valid and x.action.strip()]
        if fix:
            ctx.verdict.fix_first = fix[:7]
        questions = sorted(out.questions, key=lambda q: min(max(q.priority, 1), 3))
        ctx.questions = [
            Question(
                qid=f"Q-{i:02d}",
                text=q.text.strip(),
                addressee=q.addressee.value,
                why=q.why.strip(),
                options=[o.strip() for o in q.options if o.strip()][:4],
                recommended=q.recommended.strip(),
                finding_ids=[x for x in q.finding_ids if x in valid],
                priority=min(max(q.priority, 1), 3),
            )
            for i, q in enumerate(questions[:20], start=1)
            if q.text.strip()
        ]
    if not ctx.questions:
        ctx.questions = fallback_questions(open_findings(ctx.findings))
    if ctx.answers:
        asked = [a.question for a in ctx.answers]
        ctx.questions = [q for q in ctx.questions if all(fuzz.token_set_ratio(q.text, x) < 85 for x in asked)]
        # номера открытых вопросов продолжают номера отвеченных, чтобы ID в отчёте и в файле ответов не совпадали
        taken = [int(m.group(1)) for a in ctx.answers if (m := re.match(r"Q-(\d+)$", a.qid))]
        start = max(taken, default=0) + 1
        for i, q in enumerate(ctx.questions, start=start):
            q.qid = f"Q-{i:02d}"
    if not ctx.summary:
        s = ctx.stats.by_severity
        ctx.summary = (
            f"Выделено требований: {ctx.stats.requirements_total}, найдено дефектов: {ctx.stats.findings_total} "
            f"(CRITICAL {s.get('CRITICAL', 0)}, MAJOR {s.get('MAJOR', 0)}, MINOR {s.get('MINOR', 0)}). "
            f"Разработка — {GO_LABEL[ctx.verdict.development]}, тестирование — {GO_LABEL[ctx.verdict.testing]}."
        )
    if not ctx.verdict.rationale_dev:
        ctx.verdict.rationale_dev = _default_rationale(ctx, "development")
    if not ctx.verdict.rationale_test:
        ctx.verdict.rationale_test = _default_rationale(ctx, "testing")


def _default_rationale(ctx: RunContext, which: str) -> str:
    crit = [f.fid for f in ctx.findings if f.severity == Severity.CRITICAL]
    major = [f.fid for f in ctx.findings if f.severity == Severity.MAJOR]
    if crit:
        return f"Есть критичные дефекты: {', '.join(crit[:8])}."
    if major:
        return f"Критичных дефектов нет; существенные требуют уточнения: {', '.join(major[:8])}."
    return "Критичных и существенных дефектов не найдено."
