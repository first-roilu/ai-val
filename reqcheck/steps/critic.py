"""Шаг 7: независимая перепроверка находок (LLM) — отсев ложных, слияние дублей, калибровка."""

from __future__ import annotations

import re

from reqcheck.context import RunContext
from reqcheck.models import SEVERITY_RANK, CriticOut, Severity
from reqcheck.steps.common import ask, draft_to_finding
from reqcheck.steps.merge import assign_ids, cap_severity, merge_group, similar

_FID_RE = re.compile(r"\bD-\d{3,}\b")


def _listing(ctx: RunContext) -> str:
    blocks = []
    for f in ctx.findings:
        quotes = "; ".join(f"«{e.quote[:220]}» ({e.locator.label()})" for e in f.evidence[:3])
        rule = "[правило] " if f.hard else ""
        blocks.append(
            f"{f.fid} · {f.severity.value} · {f.criterion.value} · {rule}{f.title}\n"
            f"Суть: {f.problem[:500]}\n"
            f"Цитаты: {quotes}\n"
            f"Рекомендация: {f.recommendation[:250]}"
        )
    return "\n\n".join(blocks)


def run(ctx: RunContext) -> None:
    if not ctx.findings:
        return
    out: CriticOut = ask(ctx, "critic", "critic.md.j2", CriticOut, findings=_listing(ctx))
    first_rejected = len(ctx.rejected)
    merged_into: dict[str, str] = {}  # ID слитой находки → ID цели (нумерация до критика)
    by_id = {f.fid: f for f in ctx.findings}
    removed: set[str] = set()
    judged: set[str] = set()  # критичность задал критик — слияние её не поднимает
    # Итог не должен зависеть от порядка решений в ответе: сначала отсев и критичность, затем слияния.
    for d in out.decisions:
        f = by_id.get(d.fid)
        if f is None or f.fid in removed:
            continue
        if d.action == "drop":
            if f.hard:
                continue
            if not d.evidence_quote.strip() or ctx.grounder.locate(d.evidence_quote) is None:
                # удалить можно только с опровергающей цитатой; без неё — понижаем на уровень
                lower = {Severity.CRITICAL: Severity.MAJOR, Severity.MAJOR: Severity.MINOR}.get(f.severity)
                if lower is not None:
                    f.source.append(f"critic:{f.severity.value}→{lower.value} (без опровергающей цитаты)")
                    f.severity = lower
                    judged.add(f.fid)
                f.confidence = min(f.confidence, 0.5)
                continue
            removed.add(f.fid)
            ctx.rejected.append(
                {"source": "critic", "fid": f.fid, "title": f.title, "reason": f"{d.reason} Опровержение: «{d.evidence_quote[:160]}»"}
            )
        elif d.action == "severity" and "new_severity" in d.model_fields_set and d.new_severity != f.severity:
            if f.hard and SEVERITY_RANK[d.new_severity] < SEVERITY_RANK[f.severity]:
                continue  # детерминированные находки можно только понизить
            f.source.append(f"critic:{f.severity.value}→{d.new_severity.value}")
            f.severity = d.new_severity
            judged.add(f.fid)
    for d in out.decisions:
        f = by_id.get(d.fid)
        if d.action != "merge" or f is None or f.fid in removed:
            continue
        target = by_id.get(d.target)
        if target is None or target.fid == f.fid or target.fid in removed:
            continue
        # основной остаётся цель критика, если сливаемая находка не критичнее её или критик сам оценил цель
        more_severe = SEVERITY_RANK[f.severity] < SEVERITY_RANK[target.severity]
        base = f if more_severe and target.fid not in judged else target
        merged = merge_group([target, f], base=base)
        merged.fid = target.fid
        if target.fid in judged:
            merged.severity = target.severity  # критик сам оценил основную находку: дубль не возвращает CRITICAL
        by_id[target.fid] = merged
        removed.add(f.fid)
        merged_into[f.fid] = target.fid
        ctx.rejected.append({"source": "critic", "fid": f.fid, "title": f.title, "reason": f"слита с {target.fid}: {d.reason}"})
    findings = [by_id[f.fid] for f in ctx.findings if f.fid not in removed]
    for draft in out.missed[:3]:
        candidate = draft_to_finding(ctx, draft, "llm:critic")
        if candidate is not None and not any(similar(candidate, g) for g in findings):
            findings.append(candidate)
    for f in findings:
        cap_severity(f)
    old_ids = {id(f): f.fid for f in findings}
    assign_ids(findings)  # сортирует список на месте и перенумеровывает
    ctx.findings = findings
    # причины в «Отклонённых находках» ссылаются на нумерацию до критика — переводим на итоговую
    renamed = {old_ids[id(f)]: f.fid for f in findings if old_ids.get(id(f))}
    for old, target in merged_into.items():
        for _ in range(len(merged_into)):  # цепочка слияний: A → B → C
            if target in renamed or target not in merged_into:
                break
            target = merged_into[target]
        if target in renamed:
            renamed.setdefault(old, renamed[target])
    for entry in ctx.rejected[first_rejected:]:
        entry["reason"] = _FID_RE.sub(lambda m: renamed.get(m.group(0), m.group(0)), entry["reason"])
