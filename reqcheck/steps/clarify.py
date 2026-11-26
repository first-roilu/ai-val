"""Шаг 7a: учёт ответов заказчика (LLM) — какие дефекты сняты, какие правки внести в документ."""

from __future__ import annotations

import re

from reqcheck.context import RunContext
from reqcheck.models import Answer, ClarifyOut, Finding
from reqcheck.steps.common import ask, draft_to_finding
from reqcheck.steps.merge import similar

_FID_RE = re.compile(r"^D-(\d+)$")


def _answers_block(answers: list[Answer]) -> str:
    blocks = []
    for a in answers:
        related = f"\nДефекты, к которым задан вопрос: {', '.join(a.finding_ids)}" if a.finding_ids else ""
        blocks.append(f"### {a.qid}\nВопрос: {a.question}{related}\n<answer>\n{a.answer}\n</answer>")
    return "\n\n".join(blocks)


def _findings_block(findings: list[Finding]) -> str:
    rows = []
    for f in findings:
        quotes = "; ".join(f"«{e.quote[:200]}» ({e.locator.label()})" for e in f.evidence[:2])
        rows.append(f"{f.fid} · {f.severity.value} · {f.criterion.value} · {f.title}\nСуть: {f.problem[:400]}\nЦитаты: {quotes}")
    return "\n\n".join(rows) or "(дефектов нет)"


def run(ctx: RunContext, answers: list[Answer]) -> None:
    """Применяет ответы к находкам. Без ответов ничего не делает."""
    answers = [a for a in answers if a.answer.strip()]
    if not answers:
        return
    # повторный учёт (например, после вопросов в консоли) начинается с чистого состояния
    ctx.findings = [f for f in ctx.findings if "llm:clarify" not in f.source]
    for f in ctx.findings:
        f.resolution, f.resolution_note, f.resolved_by = "", "", []
    for a in answers:
        a.resolved, a.partial, a.still_open, a.new_findings = [], [], [], []
        a.amendment, a.where = "", ""
    ctx.answers = answers
    if not ctx.findings:
        return
    out: ClarifyOut = ask(
        ctx,
        "clarify",
        "clarify.md.j2",
        ClarifyOut,
        answers=_answers_block(answers),
        findings=_findings_block(ctx.findings),
    )
    by_fid = {f.fid: f for f in ctx.findings}
    by_qid = {a.qid: a for a in answers}
    for d in out.decisions:
        f = by_fid.get(d.fid.strip())
        if f is None:
            continue
        qids = [q for q in dict.fromkeys(x.strip() for x in d.by) if q in by_qid]
        if not qids:  # модель не указала вопрос — берём те, что были заданы к этому дефекту
            qids = [a.qid for a in answers if f.fid in a.finding_ids]
        f.resolution = d.status
        f.resolution_note = d.note.strip()
        f.resolved_by = qids
        bucket = {"resolved": "resolved", "partial": "partial", "open": "still_open"}[d.status]
        for q in qids:
            target = getattr(by_qid[q], bucket)
            if f.fid not in target:
                target.append(f.fid)
    for item in out.amendments:
        a = by_qid.get(item.qid.strip())
        if a is not None:
            a.amendment = item.wording.strip()
            a.where = item.where.strip()

    # новые дефекты, которые создают ответы, — с номерами после существующих, чтобы ID не сдвигались
    numbers = [int(m.group(1)) for f in ctx.findings if (m := _FID_RE.match(f.fid))]
    next_no = max(numbers, default=0) + 1
    for draft in out.new_findings[:5]:
        candidate = draft_to_finding(ctx, draft, "llm:clarify")
        if candidate is None or any(similar(candidate, g) for g in ctx.findings):
            continue
        candidate.fid = f"D-{next_no:03d}"
        next_no += 1
        ctx.findings.append(candidate)
        for a in answers:  # к какому ответу относится — по упоминанию в описании, иначе ко всем
            if a.qid in (draft.problem + draft.title) or len(answers) == 1:
                a.new_findings.append(candidate.fid)
