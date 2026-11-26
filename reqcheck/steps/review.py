"""Шаг 4: проверка каждого требования по 9 критериям (LLM, батчи)."""

from __future__ import annotations

from reqcheck.context import RunContext
from reqcheck.errors import LLMOutputError, LLMTruncatedError
from reqcheck.knowledge import render_checklists, render_criteria, select_checklists
from reqcheck.models import (
    INDIVIDUAL_CRITERIA,
    AssessmentDraft,
    Category,
    Criterion,
    CriterionCheck,
    FindingDraft,
    ReqAssessment,
    Requirement,
    ReviewOut,
    Scenario,
    Severity,
)
from reqcheck.steps.common import ask, batch_block, hints_block


def make_batches(requirements: list[Requirement], size: int) -> list[list[Requirement]]:
    batches: list[list[Requirement]] = []
    current: list[Requirement] = []
    for r in requirements:
        base = r.rid.split(".")[0]
        same_group = bool(current) and current[-1].rid.split(".")[0] == base
        top = r.section_path.split(" › ")[0]
        top_changed = bool(current) and current[-1].section_path.split(" › ")[0] != top
        if current and not same_group and (len(current) >= size or (top_changed and len(current) >= max(2, size // 2))):
            batches.append(current)
            current = []
        current.append(r)
    if current:
        batches.append(current)
    return batches


def run_batch(ctx: RunContext, batch: list[Requirement], label: str, depth: int = 0) -> None:
    kinds = {r.kind.value for r in batch}
    text = " ".join(f"{r.quote} {r.section_path}" for r in batch)
    lo = min(r.locator.line_start for r in batch)
    hi = max(r.locator.line_end for r in batch)
    hints = [s for s in ctx.signals if lo - 2 <= s.line <= hi + 2]
    try:
        out: ReviewOut = ask(
            ctx,
            f"review#{label}",
            "review_reqs.md.j2",
            ReviewOut,
            meta={"rids": [r.rid for r in batch]},
            criteria=render_criteria("individual"),
            checklists=render_checklists(select_checklists(text, kinds)),
            hints=hints_block(hints),
            batch=batch_block(batch),
        )
    except (LLMTruncatedError, LLMOutputError) as exc:
        if len(batch) > 1 and depth < 3:
            ctx.notify(f"{label}: {exc} — делю батч пополам")
            half = len(batch) // 2
            run_batch(ctx, batch[:half], f"{label}a", depth + 1)
            run_batch(ctx, batch[half:], f"{label}b", depth + 1)
            return
        _mark_unreviewed(ctx, batch, str(exc))
        return

    wanted = {r.rid: r for r in batch}
    got = {a.rid: a for a in out.assessments if a.rid in wanted}
    for rid, a in got.items():
        _store(ctx, wanted[rid], a)
    missing = [r for r in batch if r.rid not in got]
    if missing:
        if depth == 0 and len(missing) < len(batch):
            run_batch(ctx, missing, f"{label}r", depth + 1)
        else:
            _mark_unreviewed(ctx, missing, "модель не вернула оценку")


def _mark_unreviewed(ctx: RunContext, reqs: list[Requirement], reason: str) -> None:
    for r in reqs:
        ctx.assessments[r.rid] = ReqAssessment(
            rid=r.rid,
            checks=[CriterionCheck(criterion=c, status="na", note="не оценено") for c in INDIVIDUAL_CRITERIA],
            conclusion=f"Требование не проверено моделью: {reason}.",
            reviewed=False,
        )
    ctx.limit(f"Не проверены моделью требования {', '.join(r.rid for r in reqs)}: {reason}.")


def _store(ctx: RunContext, req: Requirement, a: AssessmentDraft) -> None:
    checks = {c.criterion: c for c in a.checks if c.criterion in INDIVIDUAL_CRITERIA}
    full = [
        CriterionCheck(criterion=c, status=checks[c].status, note=checks[c].note.strip())
        if c in checks
        else CriterionCheck(criterion=c, status="na", note="не оценено")
        for c in INDIVIDUAL_CRITERIA
    ]
    findings = list(a.findings)
    for f in findings:
        if not f.rids:
            f.rids = [req.rid]
        if not f.quotes:
            f.quotes = [req.quote]
    if not findings and any(c.status == "fail" for c in full):
        # модель отметила дефект, но не оформила находку: неопределённый результат сценария становится находкой.
        # Если все критерии ok, пробел в сценарии модель сочла несущественным — он остаётся в отчёте как «❓»
        for s in a.scenarios:
            if not s.determinable and s.gap.strip():
                findings.append(
                    FindingDraft(
                        rids=[req.rid],
                        criterion=Criterion.COMPLETE,
                        category=Category.DOMAIN,
                        severity=Severity.MAJOR,
                        title=f"Не определён результат сценария: {s.title}"[:150],
                        quotes=[req.quote],
                        problem=f"Из текста нельзя однозначно вывести ожидаемый результат: {s.gap.strip()}",
                        impact="Разработчик и тестировщик примут разные решения; критерий приёмки не написать.",
                        scenario=f"Дано: {s.given}. Действие: {s.when}.",
                        recommendation="Дополнить требование правилом для этого сценария.",
                    )
                )
    ctx.add_drafts(findings, "llm:review")
    ctx.assessments[req.rid] = ReqAssessment(
        rid=req.rid,
        checks=full,
        scenarios=[Scenario(**s.model_dump()) for s in a.scenarios[:6]],
        rewrite=a.rewrite.strip(),
        conclusion=a.conclusion.strip(),
    )
