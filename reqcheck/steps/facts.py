"""Шаг 3b (LLM) и 5d (код): факты для поиска противоречий, расчётные утверждения и их пересчёт."""

from __future__ import annotations

import re
from collections import defaultdict

from reqcheck.calc import DECISION_METRIC_RE, CalcError, compare, evaluate
from reqcheck.context import RunContext
from reqcheck.models import (
    CalcResult,
    Category,
    ClaimDraft,
    Criterion,
    Evidence,
    Fact,
    FactsOut,
    Finding,
    Severity,
)
from reqcheck.steps.common import ask, fmt_number


def run(ctx: RunContext) -> None:
    out: FactsOut = ask(ctx, "facts", "facts.md.j2", FactsOut)
    index = ctx.req_index
    for f in out.facts[:200]:
        near = ctx.rid_range([f.rid]) if f.rid in index else None
        ev = ctx.grounder.evidence(f.quote, near)
        if ev is None:
            continue
        ctx.facts.append(
            Fact(
                rid=f.rid if f.rid in index else "",
                entity=f.entity.strip(),
                attribute=f.attribute.strip(),
                value=f.value.strip(),
                unit=f.unit.strip(),
                condition=f.condition.strip(),
                evidence=ev,
            )
        )
    for c in out.claims[:80]:
        ctx.claims.append((c, ctx.grounder.evidence(c.quote)))
    ctx.trace.write_json("facts.json", ctx.facts)


def run_calc(ctx: RunContext, claims: list[tuple[ClaimDraft, Evidence | None]]) -> None:
    index = ctx.req_index
    for claim, ev in claims:
        cid = f"C-{len(ctx.calc_results) + 1:02d}"
        computed = None
        deviation = None
        note = ""
        try:
            computed = evaluate(claim.expression)
            ok, deviation = compare(claim.stated_value, computed)
            status = "ok" if ok else "mismatch"
        except CalcError as exc:
            status, note = "error", str(exc)
        result = CalcResult(
            cid=cid,
            label=claim.label.strip(),
            expression=claim.expression.strip(),
            stated=claim.stated_value,
            computed=computed,
            unit=claim.unit.strip(),
            status=status,
            deviation_pct=round(deviation, 2) if deviation is not None else None,
            note=note,
            evidence=ev,
        )
        ctx.calc_results.append(result)
        if status == "mismatch" and ev is not None:
            unit = f" {result.unit}" if result.unit else ""
            critical = bool(DECISION_METRIC_RE.search(result.label))
            rid = claim.rid if claim.rid in index else ""
            ctx.direct_findings.append(
                Finding(
                    rids=[rid] if rid else ctx.rids_for_lines(ev.locator.line_start, ev.locator.line_end),
                    scope="requirement" if rid else "set",
                    criterion=Criterion.CORRECT,
                    category=Category.CALC,
                    severity=Severity.CRITICAL if critical else Severity.MAJOR,
                    title=f"Ошибка расчёта: {result.label}"[:160],
                    problem=(
                        f"В документе заявлено {fmt_number(result.stated)}{unit}, но по приведённым в документе данным "
                        f"получается {fmt_number(computed)}{unit} (расхождение {deviation:.1f}%). "
                        f"Пересчёт: {result.expression} = {fmt_number(computed)}."
                    ),
                    impact="Неверное значение искажает бизнес-обоснование и связанные с ним KPI и решения.",
                    recommendation=f"Пересчитать «{result.label}» и все показатели, которые от него зависят.",
                    evidence=[ev],
                    source=["calc"],
                    confidence=0.8,
                )
            )


def _norm(text: str) -> str:
    text = text.lower().replace("ё", "е")
    return re.sub(r"[^\w%₸$€]+", " ", text).strip()


def fact_groups(ctx: RunContext, limit: int = 30) -> list[tuple[str, list[Fact]]]:
    canonical: dict[str, str] = {}
    for term in ctx.glossary:
        for variant in [term.canonical, *term.variants]:
            if variant:
                canonical[_norm(variant)] = _norm(term.canonical)
    groups: dict[tuple[str, str], list[Fact]] = defaultdict(list)
    for f in ctx.facts:
        entity = canonical.get(_norm(f.entity), _norm(f.entity))
        groups[(entity, _norm(f.attribute))].append(f)
    result = []
    for (entity, attribute), facts in groups.items():
        values = {(_norm(f.value), _norm(f.unit)) for f in facts}
        if len(facts) >= 2 and len(values) >= 2:
            result.append((f"{facts[0].entity} / {facts[0].attribute}", facts))
    result.sort(key=lambda g: -len(g[1]))
    return result[:limit]


def render_groups(groups: list[tuple[str, list[Fact]]]) -> str:
    if not groups:
        return "(расхождений между фактами не найдено — проверь взаимодействие правил и критерии приёмки)"
    blocks = []
    for i, (title, facts) in enumerate(groups, start=1):
        rows = [f"Группа G{i} — {title}:"]
        for f in facts[:6]:
            where = f.rid or "вне реестра"
            unit = f" {f.unit}" if f.unit else ""
            cond = f" [при: {f.condition}]" if f.condition else ""
            rows.append(f"  - {where}: «{f.value}{unit}»{cond} — «{f.evidence.quote[:160]}» ({f.evidence.locator.label()})")
        blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def render_calc(ctx: RunContext) -> str:
    if not ctx.calc_results:
        return "(расчётов в документе не найдено)"
    rows = ["| № | Показатель | Выражение | Заявлено | Пересчёт | Статус |", "|---|---|---|---|---|---|"]
    for r in ctx.calc_results:
        rows.append(
            f"| {r.cid} | {r.label} | `{r.expression}` | {fmt_number(r.stated)} | {fmt_number(r.computed)} | {r.status}{(': ' + r.note) if r.note else ''} |"
        )
    return "\n".join(rows)
