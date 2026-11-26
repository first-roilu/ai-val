"""Шаг 6: сбор находок из всех источников, сверка цитат, дедупликация, присвоение ID."""

from __future__ import annotations

import re
from collections import Counter

from rapidfuzz import fuzz

from reqcheck.context import RunContext
from reqcheck.models import SET_CRITERIA, SEVERITY_RANK, Criterion, Evidence, Finding, Severity, Signal
from reqcheck.steps.common import draft_to_finding

FAMILY: dict[Criterion, str] = {
    Criterion.COMPLETE: "полнота",
    Criterion.SET_COMPLETE: "полнота",
    Criterion.SET_CONSISTENT: "согласованность",
    Criterion.CORRECT: "согласованность",
    Criterion.UNAMBIGUOUS: "однозначность",
    Criterion.SET_COMPREHENSIBLE: "однозначность",
    Criterion.VERIFIABLE: "проверяемость",
    Criterion.SET_VALIDATABLE: "проверяемость",
    Criterion.FEASIBLE: "осуществимость",
    Criterion.SET_FEASIBLE: "осуществимость",
    Criterion.CONFORMING: "соответствие",
    Criterion.SET_TRACEABLE: "трассировка",
    Criterion.SET_MODIFIABLE: "структура",
    Criterion.SET_RANKED: "структура",
    Criterion.APPROPRIATE: "уместность",
    Criterion.NECESSARY: "уместность",
    Criterion.SINGULAR: "единичность",
}
# Рубрика: CRITICAL — противоречие, непроверяемое ключевое требование, нарушение протокола или стандарта,
# отсутствие необходимого для реализации, ошибка расчёта. Двусмысленность, избыточность, составность,
# терминология и структура документа по рубрике — не выше MAJOR.
CRITICAL_ALLOWED = {
    Criterion.SET_CONSISTENT,
    Criterion.CORRECT,
    Criterion.VERIFIABLE,
    Criterion.SET_VALIDATABLE,
    Criterion.COMPLETE,
    Criterion.SET_COMPLETE,
    Criterion.FEASIBLE,
    Criterion.SET_FEASIBLE,
    Criterion.CONFORMING,
}

SOURCE_PRIORITY = ("llm:lens_consistency", "llm:lens_standards", "llm:lens_business", "llm:review", "llm:lens_completeness", "llm:critic", "calc", "rule")


def _draft_key(item) -> tuple:
    draft, source = item
    return (source, sorted(draft.rids), draft.criterion.value, draft.title, draft.problem, tuple(draft.quotes))


def run(ctx: RunContext) -> None:
    findings: list[Finding] = [signal_to_finding(ctx, s) for s in ctx.signals if s.hard]
    # шаги выполняются параллельно и складывают черновики в порядке завершения; сортировка делает
    # слияние и ID воспроизводимыми, а значит, повторный прогон попадает в кэш критика и синтеза
    for draft, source in sorted(ctx.drafts, key=_draft_key):
        finding = draft_to_finding(ctx, draft, source)
        if finding is not None:
            findings.append(finding)
    findings.extend(sorted(ctx.direct_findings, key=lambda f: (f.source, f.rids, f.title, f.problem)))
    for f in findings:
        cap_severity(f)
    ctx.findings = dedupe(findings)
    assign_ids(ctx.findings)


def cap_severity(f: Finding) -> None:
    """Потолок критичности по рубрике: CRITICAL только для классов, которые блокируют разработку."""
    if f.severity == Severity.CRITICAL and f.criterion not in CRITICAL_ALLOWED and not f.hard:
        f.source.append(f"рубрика:CRITICAL→MAJOR ({f.criterion.value})")
        f.severity = Severity.MAJOR


def signal_to_finding(ctx: RunContext, s: Signal) -> Finding:
    locator = ctx.grounder.locator(s.line, s.line)
    return Finding(
        rids=ctx.rids_for_lines(s.line, s.line),
        scope="set" if s.criterion in SET_CRITERIA else "requirement",
        criterion=s.criterion,
        category=s.category,
        severity=s.severity,
        title=s.title or s.message[:120],
        problem=s.message,
        recommendation=s.recommendation,
        evidence=[Evidence(quote=s.quote, locator=locator)],
        source=[f"rule:{s.rule_id}"],
        confidence=0.9,
        hard=True,
    )


def _overlap(a: Finding, b: Finding) -> bool:
    for x in a.evidence:
        for y in b.evidence:
            if x.locator.line_start <= y.locator.line_end and y.locator.line_start <= x.locator.line_end:
                return True
    return False


_CODE_RE = re.compile(r"(?<![\w-])(?:[A-Za-z][A-Za-z0-9]*(?:[-_./][A-Za-z0-9]+)*|[1-5]\d\d)(?![\w-])")
_COMMON_CODES = {
    "api", "http", "https", "json", "rest", "url", "id", "ui", "ok", "push", "sla", "nfr", "db", "sql",
    "iso", "iec", "ieee", "incose", "ears", "kzt", "error", "true", "false", "null", "string", "integer",
}
_REQ_ID_RE = re.compile(r"^(req|ф|f|nfr|br|fr)-?\d", re.IGNORECASE)


def codes(f: Finding) -> set[str]:
    """Технические «метки» находки: методы, поля, коды ответов, заголовки (GET, cardId, 201, Content-Type)."""
    text = f"{f.title} {f.problem[:300]}"
    return {
        t.lower()
        for t in _CODE_RE.findall(text)
        if t.lower() not in _COMMON_CODES and not _REQ_ID_RE.match(t) and len(t) > 1
    }


def similar(a: Finding, b: Finding, rare: set[str] | None = None) -> bool:
    if not _overlap(a, b):
        return False
    if fuzz.token_set_ratio(a.title, b.title) >= 85:
        return True
    if rare and (codes(a) & codes(b) & rare):
        # одна причина под разными критериями: те же строки и та же редкая техническая метка
        # (GET для сохранения как ОСУЩ, КОРР и СООТВ; Content-Type при пустом теле)
        return True
    same_family = FAMILY.get(a.criterion) == FAMILY.get(b.criterion)
    if not same_family:
        return False
    if a.hard or b.hard:
        return True
    text_a = f"{a.title} {a.problem[:200]}"
    text_b = f"{b.title} {b.problem[:200]}"
    return fuzz.token_set_ratio(text_a, text_b) >= 62


def _source_rank(f: Finding) -> int:
    for i, prefix in enumerate(SOURCE_PRIORITY):
        if any(src.startswith(prefix) for src in f.source):
            return i
    return len(SOURCE_PRIORITY)


def merge_group(members: list[Finding], base: Finding | None = None) -> Finding:
    # текст берётся у самой критичной находки: заголовок мелочи с критичностью CRITICAL прячет главный дефект
    if base is None:
        base = min(members, key=lambda f: (SEVERITY_RANK[f.severity], f.hard, _source_rank(f), -len(f.problem)))
    severity = min((f.severity for f in members), key=lambda s: SEVERITY_RANK[s])
    evidence: list[Evidence] = []
    seen = set()
    for f in [base, *[m for m in members if m is not base]]:
        for ev in f.evidence:
            key = (ev.locator.line_start, ev.locator.line_end)
            if key not in seen:
                seen.add(key)
                evidence.append(ev)
    rids = list(dict.fromkeys(r for f in [base, *members] for r in f.rids))
    questions = list(dict.fromkeys(q for f in [base, *members] for q in f.questions if q))
    sources = list(dict.fromkeys(s for f in [base, *members] for s in f.source))
    return base.model_copy(
        update={
            "severity": severity,
            "evidence": evidence[:5],
            "rids": rids[:8],
            "questions": questions[:3],
            "source": sources,
            "hard": any(f.hard for f in members),
            "confidence": max(f.confidence for f in members),
            "scenario": base.scenario or next((f.scenario for f in members if f.scenario), ""),
            "impact": base.impact or next((f.impact for f in members if f.impact), ""),
            "recommendation": base.recommendation or next((f.recommendation for f in members if f.recommendation), ""),
        }
    )


def dedupe(findings: list[Finding]) -> list[Finding]:
    parent = list(range(len(findings)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # метка «редкая», если встречается не больше чем в 5 находках и не больше чем в 15% из них
    counts = Counter(code for f in findings for code in codes(f))
    limit = max(2, min(5, int(len(findings) * 0.15)))
    rare = {code for code, n in counts.items() if n <= limit}
    for i in range(len(findings)):
        for j in range(i + 1, len(findings)):
            if find(i) != find(j) and similar(findings[i], findings[j], rare):
                parent[find(j)] = find(i)
    groups: dict[int, list[Finding]] = {}
    for i, f in enumerate(findings):
        groups.setdefault(find(i), []).append(f)
    return [merge_group(g) if len(g) > 1 else g[0] for g in groups.values()]


def assign_ids(findings: list[Finding]) -> None:
    findings.sort(key=lambda f: (SEVERITY_RANK[f.severity], f.evidence[0].locator.line_start if f.evidence else 10**6))
    for i, f in enumerate(findings, start=1):
        f.fid = f"D-{i:03d}"
