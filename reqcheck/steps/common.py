"""Общие помощники шагов: блоки контекста для промптов, вызов модели, перевод черновиков в находки."""

from __future__ import annotations

from pydantic import BaseModel

from reqcheck import prompts
from reqcheck.context import RunContext
from reqcheck.knowledge import render_severity
from reqcheck.llm.base import LLMRequest
from reqcheck.models import (
    KIND_LABEL,
    SET_CRITERIA,
    Finding,
    FindingDraft,
    Requirement,
    Signal,
)


def document_block(ctx: RunContext) -> str:
    return ctx.doc.numbered()


def registry_block(ctx: RunContext) -> str:
    if not ctx.requirements:
        return "(реестр пуст)"
    return "\n".join(registry_line(r) for r in ctx.requirements)


def registry_line(r: Requirement) -> str:
    lines = (
        f"строка {r.locator.line_start}"
        if r.locator.line_start == r.locator.line_end
        else f"строки {r.locator.line_start}–{r.locator.line_end}"
    )
    return f"{r.rid} · {r.doc_id_label} · {r.kind.value} · {lines} · {r.section_path} — {r.summary}"


def batch_block(batch: list[Requirement]) -> str:
    blocks = []
    for r in batch:
        lines = (
            f"строка {r.locator.line_start}"
            if r.locator.line_start == r.locator.line_end
            else f"строки {r.locator.line_start}–{r.locator.line_end}"
        )
        blocks.append(
            f"### {r.rid} · ID в документе: {r.doc_id_label} · тип: {r.kind.value} ({KIND_LABEL[r.kind]}) · {lines}\n"
            f"Раздел: {r.section_path}\n"
            f"Цитата: «{r.quote}»"
        )
    return "\n\n".join(blocks)


def glossary_block(ctx: RunContext) -> str:
    if not ctx.glossary:
        return "(глоссарий не выделен)"
    rows = []
    for t in ctx.glossary:
        variants = ", ".join(f"«{v}»" for v in t.variants if v and v != t.canonical)
        defined = "есть определение" if t.defined_in_doc else "без определения"
        rows.append(f"- {t.canonical}" + (f" — варианты: {variants}" if variants else "") + f" ({defined})")
    return "\n".join(rows)


def hints_block(signals: list[Signal], limit: int = 40) -> str:
    if not signals:
        return "(нет)"
    rows = [f"- строка {s.line} [{s.rule_id}]: {s.message}" for s in signals[:limit]]
    if len(signals) > limit:
        rows.append(f"- … и ещё {len(signals) - limit}")
    return "\n".join(rows)


def base_vars(ctx: RunContext) -> dict:
    return {
        "document": document_block(ctx),
        "registry": registry_block(ctx),
        "glossary": glossary_block(ctx),
        "severity": render_severity(),
        "outline": ctx.doc.outline() or "(разделы не выделены)",
    }


def ask(ctx: RunContext, step: str, template: str, schema: type[BaseModel], *, meta: dict | None = None, **variables) -> BaseModel:
    context = base_vars(ctx)
    context.update(variables)
    user = prompts.render(template, **context)
    ctx.notify(f"→ {step}")
    result = ctx.llm.generate(
        LLMRequest(
            step=step,
            system=ctx.system_prompt,
            user=user,
            schema=schema,
            max_output_tokens=ctx.settings.output_tokens,
            meta=meta or {},
        )
    )
    return result.data


def draft_to_finding(ctx: RunContext, draft: FindingDraft, source: str) -> Finding | None:
    """Сверяет цитаты черновика с документом; без подтверждённых цитат находка отклоняется."""
    if not draft.title.strip() and not draft.problem.strip():
        ctx.rejected.append({"source": source, "title": "", "reason": "нет ни названия, ни описания", "quotes": draft.quotes[:3]})
        return None
    near = ctx.rid_range(draft.rids)
    evidence, missing = ctx.grounder.evidences([q for q in draft.quotes[:4] if q], near)
    if not evidence:
        ctx.rejected.append(
            {
                "source": source,
                "title": draft.title,
                "reason": "цитаты не найдены в документе",
                "quotes": draft.quotes[:3],
            }
        )
        return None
    index = ctx.req_index
    rids = [r for r in dict.fromkeys(draft.rids) if r in index]
    if not rids:
        for ev in evidence:
            rids.extend(r for r in ctx.rids_for_lines(ev.locator.line_start, ev.locator.line_end) if r not in rids)
    question = draft.question.strip()
    return Finding(
        rids=rids[:8],
        scope="set" if draft.criterion in SET_CRITERIA else "requirement",
        criterion=draft.criterion,
        category=draft.category,
        severity=draft.severity,
        title=draft.title.strip()[:180] or _title_from(draft.problem) or "Дефект без названия",
        problem=draft.problem.strip() or draft.title.strip(),
        impact=draft.impact.strip(),
        scenario=draft.scenario.strip(),
        recommendation=draft.recommendation.strip(),
        evidence=evidence[:4],
        questions=[question] if question else [],
        source=[source],
        confidence=0.75 if not missing else 0.6,
    )


def fmt_number(value: float | None) -> str:
    if value is None:
        return "—"
    if abs(value - round(value)) < 1e-9:
        return f"{int(round(value)):,}".replace(",", " ")
    text = f"{value:,.4f}".rstrip("0").rstrip(".")
    return text.replace(",", " ").replace(".", ",")


def _title_from(problem: str) -> str:
    """Запасное название дефекта: первая фраза описания, до 12 слов."""
    first = problem.strip().split(". ")[0]
    words = first.split()
    return " ".join(words[:12]) + ("…" if len(words) > 12 else "")
