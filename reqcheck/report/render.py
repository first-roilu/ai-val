"""Report → Markdown (шаблон Jinja2) и JSON. Структура отчёта задаётся кодом, а не моделью."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

from reqcheck.context import RunContext
from reqcheck.models import (
    CRITERION_NAME,
    CRITERION_STANDARD,
    INDIVIDUAL_CRITERIA,
    KIND_LABEL,
    SEVERITY_LABEL,
    SEVERITY_RANK,
    CriterionCheck,
    Finding,
    Locator,
    ReqAssessment,
    Report,
    RunPassport,
    Severity,
)
from reqcheck.steps.common import fmt_number
from reqcheck.steps.synthesis import set_properties
from reqcheck.version import agent_version

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
PROVIDER_LABEL = {"gemini": "Google Gemini API", "openai": "OpenAI-совместимый API", "fake": "офлайн (без модели)"}
GO_VIEW = {"go": "✅ Можно", "conditional": "⚠️ Условно, после уточнений", "no_go": "⛔ Нельзя"}
STATUS_ICON = {"ok": "✅", "warn": "⚠️", "fail": "❌", "na": "—"}
SEV_ICON = {Severity.CRITICAL: "🔴", Severity.MAJOR: "🟡", Severity.MINOR: "🟢"}


def _model_label(ctx: RunContext) -> str:
    """Модель из настроек и, если ответы давали другие модели (резервные или псевдонимы), — какие и сколько."""
    primary = ctx.llm.model or ctx.settings.model
    used = {m: n for m, n in (getattr(ctx.trace, "models", {}) or {}).items() if m}
    if not used or all(m.startswith(primary) for m in used):
        return primary
    total = sum(used.values())
    parts = ", ".join(f"{m} — {n}" for m, n in sorted(used.items(), key=lambda kv: -kv[1]))
    return f"{primary} по настройке; ответы дали: {parts} (из {total})"


def build_report(ctx: RunContext) -> Report:
    passport = RunPassport(
        document=ctx.doc.name,
        document_path=ctx.doc.path,
        document_format=ctx.doc.fmt,
        sha256=ctx.doc.sha256,
        started_at=ctx.started,
        finished_at=datetime.now().astimezone(),
        provider=ctx.settings.provider,
        model=_model_label(ctx),
        temperature=ctx.settings.temperature,
        seed=ctx.settings.seed,
        thinking=ctx.settings.thinking,
        agent_version=agent_version(),
        llm_calls=ctx.trace.calls,
        cache_hits=ctx.trace.cache_hits,
        tokens_in=ctx.trace.tokens_in,
        tokens_out=ctx.trace.tokens_out,
        run_dir=str(ctx.trace.dir).replace("\\", "/"),
        text_integrity=_integrity(ctx),
        limitations=list(ctx.limitations),
    )
    assessments = []
    for r in ctx.requirements:
        a = ctx.assessments.get(r.rid)
        if a is None:
            a = ReqAssessment(
                rid=r.rid,
                checks=[CriterionCheck(criterion=c, status="na", note="не оценено") for c in INDIVIDUAL_CRITERIA],
                conclusion="Требование не проверено моделью.",
                reviewed=False,
            )
        assessments.append(a)
    return Report(
        passport=passport,
        summary=ctx.summary,
        requirements=ctx.requirements,
        assessments=assessments,
        findings=ctx.findings,
        set_properties=set_properties(ctx),
        trace_matrix=ctx.trace_rows,
        calc_checks=ctx.calc_results,
        questions=ctx.questions,
        stats=ctx.stats,
        verdict=ctx.verdict,
        glossary=ctx.glossary,
        rejected=ctx.rejected,
        answers=ctx.answers,
    )


def _integrity(ctx: RunContext) -> str:
    report = ctx.doc.repair
    parts = []
    if ctx.doc.encoding:
        parts.append(f"кодировка {ctx.doc.encoding}")
    if report is None or not report.has_changes:
        parts.append("повреждений текста не найдено")
        return "; ".join(parts)
    if report.fixed_lines:
        parts.append(f"восстановлено строк: {report.fixed_lines}")
    if report.broken:
        parts.append(f"повреждено без восстановления: {len({no for no, _, _ in report.broken})}")
    if report.invisible_removed:
        parts.append(f"удалено невидимых символов: {report.invisible_removed}")
    return "; ".join(parts)


# ───────────────────────────── фильтры шаблона ─────────────────────────────


def _cell(text) -> str:
    text = "" if text is None else str(text)
    return text.replace("\\|", "|").replace("|", "\\|").replace("\r", "").replace("\n", "<br>").strip()


def _trunc(text, limit: int = 160) -> str:
    text = "" if text is None else str(text)
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _nodot(text) -> str:
    """Без точки и многоточия в конце: фраза встраивается в список через «;» и «—»."""
    text = "" if text is None else str(text).strip()
    return text.rstrip(" .…;")


def _anchor(rid: str) -> str:
    return re.sub(r"[^a-z0-9-]+", "-", rid.lower()).strip("-")


def _loc(locator: Locator) -> str:
    return locator.label()


def _dt(value: datetime | None) -> str:
    if value is None:
        return "—"
    offset = value.strftime("%z")
    tz = f"UTC{offset[:3]}:{offset[3:]}" if offset else ""
    return f"{value.strftime('%Y-%m-%d %H:%M:%S')} ({tz})" if tz else value.strftime("%Y-%m-%d %H:%M:%S")


def _env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR), encoding="utf-8"),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
    )
    env.filters.update(
        cell=_cell,
        trunc=_trunc,
        nodot=_nodot,
        anchor=_anchor,
        loc=_loc,
        dt=_dt,
        num=lambda v: fmt_number(v) if isinstance(v, (int, float)) else str(v),
        kind=lambda k: KIND_LABEL.get(k, str(k)),
        crit=lambda c: f"{CRITERION_NAME[c]} ({c.value})",
        critname=lambda c: CRITERION_NAME[c],
        std=lambda c: CRITERION_STANDARD[c],
        sev=lambda s: SEVERITY_LABEL[s],
        sevicon=lambda s: SEV_ICON[s],
        go=lambda s: GO_VIEW[s],
        status=lambda s: STATUS_ICON.get(s, s),
        provider=lambda p: PROVIDER_LABEL.get(p, p),
    )
    return env


# ───────────────────────────── представление ─────────────────────────────


def _view(report: Report) -> dict:
    by_rid: dict[str, list[Finding]] = {}
    for f in report.findings:
        for rid in f.card_rids:
            by_rid.setdefault(rid, []).append(f)
    for items in by_rid.values():
        items.sort(key=lambda f: SEVERITY_RANK[f.severity])
    assessments = {a.rid: a for a in report.assessments}

    matrix = []
    for r in report.requirements:
        a = assessments[r.rid]
        checks = {c.criterion: c for c in a.checks}
        cells = []
        for criterion in INDIVIDUAL_CRITERIA:
            related = [f for f in by_rid.get(r.rid, []) if f.criterion == criterion]
            if any(f.severity != Severity.MINOR for f in related):
                cells.append("❌")
            elif related:
                cells.append("⚠️")
            else:
                status = checks.get(criterion).status if criterion in checks else "na"
                cells.append({"ok": "✅", "warn": "⚠️", "fail": "⚠️"}.get(status, "—"))
        found = by_rid.get(r.rid, [])
        if found:
            worst = found[0].severity
            total = f"{SEV_ICON[worst]} {len(found)}"
        elif not a.reviewed:
            total = "не проверено"
        elif all(c.status == "na" for c in a.checks):
            total = "—"
        else:
            total = "✅"
        matrix.append({"req": r, "cells": cells, "total": total})

    cards = [
        {"req": r, "assessment": assessments[r.rid], "findings": by_rid[r.rid]}
        for r in report.requirements
        if by_rid.get(r.rid)
    ]
    clean = [{"req": r, "assessment": assessments[r.rid]} for r in report.requirements if not by_rid.get(r.rid)]
    contradictions = [f for f in report.findings if f.criterion.value == "Н-СОГЛ"]
    unbound = [f for f in report.findings if not f.rids]
    duration = ""
    if report.passport.finished_at:
        seconds = int((report.passport.finished_at - report.passport.started_at).total_seconds())
        duration = f"{seconds // 60} мин {seconds % 60} с" if seconds >= 60 else f"{seconds} с"
    critical = {
        "Противоречивые": report.stats.critical_contradictory,
        "Непроверяемые": report.stats.critical_unverifiable,
        "Неполные": report.stats.critical_incomplete,
        "Прочие критичные": report.stats.critical_other,
    }
    major_classes = {
        "противоречивые": [f.fid for f in report.findings if f.severity == Severity.MAJOR and f.criterion.value == "Н-СОГЛ"],
        "непроверяемые": [f.fid for f in report.findings if f.severity == Severity.MAJOR and f.criterion.value in ("ПРОВ", "Н-ВАЛИД")],
        "неполные": [f.fid for f in report.findings if f.severity == Severity.MAJOR and f.criterion.value in ("ПОЛН", "Н-ПОЛН")],
    }
    return {
        "report": report,
        "P": report.passport,
        "S": report.stats.by_severity,
        "matrix": matrix,
        "cards": cards,
        "clean": clean,
        "contradictions": contradictions,
        "unbound": unbound,
        "duration": duration or "—",
        "criteria": INDIVIDUAL_CRITERIA,
        "critical": critical,
        "major_classes": major_classes,
        "titles": {f.fid: f.title for f in report.findings},
        # отметка в сводке: дефект снят ответом заказчика — список критичных не должен выглядеть как прежде
        "marks": {
            f.fid: {"resolved": " (✅ снят ответом)", "partial": " (◐ снят частично)"}.get(f.resolution, "")
            for f in report.findings
        },
        "offline": report.passport.provider == "fake",
    }


def render_markdown(report: Report) -> str:
    text = _env().get_template("report.md.j2").render(**_view(report))
    return re.sub(r"\n{3,}", "\n\n", text).strip() + "\n"


def write_outputs(report: Report, output: str | Path) -> tuple[Path, Path]:
    md_path = Path(output)
    if md_path.suffix.lower() not in (".md", ".markdown"):
        md_path = md_path.with_suffix(".md") if md_path.suffix.lower() != ".json" else md_path.with_suffix(".md")
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path = md_path.with_suffix(".json")
    json_path.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return md_path, json_path
