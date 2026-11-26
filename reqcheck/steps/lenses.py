"""Шаг 5: проверка набора требований — линзы непротиворечивости, полноты, стандартов, бизнес-обоснования."""

from __future__ import annotations

import re

from reqcheck.context import RunContext
from reqcheck.knowledge import render_checklists, select_checklists
from reqcheck.models import (
    BusinessOut,
    CompletenessOut,
    ConsistencyOut,
    Criterion,
    StandardsOut,
    TraceRow,
)
from reqcheck.steps.common import ask, hints_block
from reqcheck.steps.facts import fact_groups, render_calc, render_groups, run_calc

BUSINESS_RE = re.compile(r"выручк|маржа|маржинал|затрат|прибыл|окупаем|\bnpv\b|\birr\b|\broi\b|бюджет|capex|opex|юнит-экономик|unit-эконом", re.IGNORECASE)


def _kinds(ctx: RunContext) -> set[str]:
    return {r.kind.value for r in ctx.requirements}


def _hints(ctx: RunContext, rule_prefixes: tuple[str, ...]) -> str:
    return hints_block([s for s in ctx.signals if s.rule_id.startswith(rule_prefixes)])


def _store_properties(ctx: RunContext, props) -> None:
    for p in props:
        if p.criterion.value.startswith("Н-"):
            ctx.properties[p.criterion.value] = p


def consistency(ctx: RunContext) -> None:
    out: ConsistencyOut = ask(
        ctx,
        "lens:consistency",
        "lens_consistency.md.j2",
        ConsistencyOut,
        groups=render_groups(fact_groups(ctx)),
        hints=_hints(ctx, ("R-TYPES", "R-UNITS", "R-CURRENCY", "R-STATUS", "R-BODY")),
    )
    ctx.add_drafts(out.findings, "llm:lens_consistency")
    ctx.group_verdicts.extend(out.checked_groups)
    _store_properties(ctx, out.properties)


def completeness(ctx: RunContext) -> None:
    text = ctx.doc.text
    out: CompletenessOut = ask(
        ctx,
        "lens:completeness",
        "lens_completeness.md.j2",
        CompletenessOut,
        checklists=render_checklists(select_checklists(text, _kinds(ctx) | {"process"}, limit=4)),
        hints=_hints(ctx, ("R-EMPTY", "R-PLACEHOLDER", "R-ID", "R-VERSION", "R-CHECKLIST", "R-APPROVALS", "R-STATUS-UNDECLARED", "R-DUPLICATE", "R-BROKEN", "R-REPAIRED")),
    )
    ctx.add_drafts(out.findings, "llm:lens_completeness")
    index = ctx.req_index
    for row in out.trace:
        if row.rid not in index:
            continue
        ctx.trace_rows.append(
            TraceRow(
                rid=row.rid,
                acceptance=[r for r in row.acceptance if r in index],
                api=[r for r in row.api if r in index],
                data=[r for r in row.data if r in index],
                ui=[r for r in row.ui if r in index],
                gaps=row.gaps.strip(),
            )
        )
    _store_properties(ctx, out.properties)


def standards(ctx: RunContext) -> None:
    text = ctx.doc.text
    lists = [cl for cl in select_checklists(text, _kinds(ctx), limit=6) if cl["id"] != "doc_hygiene"]
    out: StandardsOut = ask(
        ctx,
        "lens:standards",
        "lens_standards.md.j2",
        StandardsOut,
        checklists=render_checklists(lists),
        hints=_hints(ctx, ("R-HTTP", "R-STATUS", "R-BODY", "R-TYPES", "R-UNITS", "R-CURRENCY", "R-INJECTION", "R-VAGUE")),
    )
    ctx.add_drafts(out.findings, "llm:lens_standards")
    _store_properties(ctx, out.properties)


def needs_business(ctx: RunContext) -> bool:
    """Линза бизнес-обоснования — только если в документе есть экономика: несколько расчётов или
    хотя бы два разных бизнес-термина («личный бюджет» в тексте о расходах клиента — не бизнес-кейс)."""
    terms = {m.group(0).lower()[:5] for m in BUSINESS_RE.finditer(ctx.doc.text)}
    return len(ctx.claims) >= 3 or len(terms) >= 2


def business(ctx: RunContext) -> None:
    out: BusinessOut = ask(ctx, "lens:business", "lens_business.md.j2", BusinessOut, calc=render_calc(ctx))
    ctx.add_drafts(out.findings, "llm:lens_business")
    extra = [(c, ctx.grounder.evidence(c.quote)) for c in out.extra_claims[:30]]
    if extra:
        run_calc(ctx, extra)


SET_PROPERTY_ORDER = [
    Criterion.SET_COMPLETE,
    Criterion.SET_CONSISTENT,
    Criterion.SET_FEASIBLE,
    Criterion.SET_COMPREHENSIBLE,
    Criterion.SET_VALIDATABLE,
    Criterion.SET_RANKED,
    Criterion.SET_MODIFIABLE,
    Criterion.SET_TRACEABLE,
]
