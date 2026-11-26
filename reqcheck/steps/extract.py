"""Шаг 3: реестр требований (LLM) + сверка цитат + проверка покрытия нарезки."""

from __future__ import annotations

import re
from collections import Counter

from reqcheck.context import RunContext
from reqcheck.grounding import Match
from reqcheck.ingest.structure import FENCE_RE
from reqcheck.models import ExtractFixOut, ExtractOut, Locator, ReqDraft, Requirement, Term
from reqcheck.rules.base import is_heading, is_separator_row
from reqcheck.rules.duplicates import find_duplicates
from reqcheck.rules.identifiers import DEF_CELL_RE, DEF_LINE_RE, IGNORED_PREFIXES
from reqcheck.steps.common import ask

NORMATIVE_RE = re.compile(
    r"\b(?:долж(?:ен|на|но|ны)|обязан\w*|необходимо|требуется|не\s+допуска\w+|запрещ\w+|допуска\w+|"
    r"обеспечива\w+|поддержива\w+|возвраща\w+|переда(?:ёт|ет|ют)|хран(?:ит|ить|ятся)|отправля\w+|"
    r"провер(?:ить|яет|яется)|исключа\w+|вход(?:ят|ит)|получа\w+|определя\w+)\b",
    re.IGNORECASE,
)
REQUIREMENT_SECTION_RE = re.compile(r"требован|логик|правил|критери|статус|параметр|ответ|сценари|функц|story|истори", re.IGNORECASE)
SKIP_SECTION_RE = re.compile(r"общие сведения|ревью|согласующ|оглавлен|история изменений|содержание", re.IGNORECASE)


def run(ctx: RunContext) -> None:
    ids = _document_ids(ctx)
    id_hint = (
        "В документе есть идентификаторы пунктов: " + ", ".join(ids[:60]) + ". Используй их как doc_id."
        if ids
        else "Явных идентификаторов пунктов не найдено — оставляй doc_id пустым."
    )
    meta = {"lines": [(ln.no, ln.text, ctx.doc.section_path_of(ln.no)) for ln in ctx.doc.lines]}
    out: ExtractOut = ask(ctx, "extract", "extract.md.j2", ExtractOut, meta=meta, id_hint=id_hint)

    grounded, failed = _ground(ctx, out.requirements)
    if failed and not ctx.offline:
        fix: ExtractFixOut = ask(ctx, "extract:fix", "extract_fix.md.j2", ExtractFixOut, mode="quotes", items=failed[:40])
        more, still_failed = _ground(ctx, fix.requirements)
        grounded += more
        failed = still_failed
    for d in failed:
        ctx.rejected.append({"source": "llm:extract", "title": d.summary, "reason": "цитата требования не найдена в документе", "quotes": [d.quote]})

    if not ctx.offline and not ctx.settings.quick:
        uncovered = _uncovered_lines(ctx, grounded)
        if uncovered:
            items = [f"{no:04d}| {text}" for no, text in uncovered[:60]]
            extra: ExtractFixOut = ask(ctx, "extract:coverage", "extract_fix.md.j2", ExtractFixOut, mode="coverage", items=items)
            more, _ = _ground(ctx, extra.requirements)
            grounded += more

    ctx.requirements = _assign_ids(ctx, grounded)
    ctx.glossary = [Term(canonical=t.canonical, variants=t.variants, defined_in_doc=t.defined_in_doc) for t in out.glossary if t.canonical]
    ctx.signals.extend(find_duplicates(ctx.requirements))
    ctx.trace.write_json("registry.json", ctx.requirements)
    if not ctx.requirements:
        ctx.limit("Модель не выделила ни одного требования, подтверждённого цитатой.")


def _document_ids(ctx: RunContext) -> list[str]:
    ids: list[str] = []
    for line in ctx.doc.lines:
        m = DEF_LINE_RE.match(line.text) or DEF_CELL_RE.match(line.text)
        if m and m.group(1).rsplit("-", 1)[0] not in IGNORED_PREFIXES and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def _ground(ctx: RunContext, drafts: list[ReqDraft]) -> tuple[list[tuple[ReqDraft, Match]], list[ReqDraft]]:
    ok: list[tuple[ReqDraft, Match]] = []
    failed: list[ReqDraft] = []
    for d in drafts:
        if not d.quote.strip():
            failed.append(d)
            continue
        m = ctx.grounder.locate(d.quote)
        if m is None:
            failed.append(d)
        else:
            ok.append((d, m))
    return ok, failed


def _uncovered_lines(ctx: RunContext, grounded: list[tuple[ReqDraft, Match]]) -> list[tuple[int, str]]:
    covered: set[int] = set()
    for _, m in grounded:
        covered.update(range(m.line_start, m.line_end + 1))
    result = []
    in_code = False
    for line in ctx.doc.lines:
        if FENCE_RE.match(line.text):
            in_code = not in_code
            continue
        text = line.text.strip()
        if in_code or not text or line.no in covered or is_heading(text) or is_separator_row(text):
            continue
        path = ctx.doc.section_path_of(line.no)
        if SKIP_SECTION_RE.search(path):
            continue
        is_item = bool(re.match(r"^(?:[-*+]|\d+\.)\s+", text)) or (text.startswith("|") and text.count("|") >= 3)
        if NORMATIVE_RE.search(text) or (is_item and REQUIREMENT_SECTION_RE.search(path)):
            if len(text) >= 15:
                result.append((line.no, line.text))
    return result


def _assign_ids(ctx: RunContext, grounded: list[tuple[ReqDraft, Match]]) -> list[Requirement]:
    # сортировка по месту, удаление дублей
    seen: set[tuple] = set()
    unique: list[tuple[ReqDraft, Match]] = []
    for d, m in sorted(grounded, key=lambda x: (x[1].line_start, x[0].part, x[1].line_end)):
        key = (m.line_start, m.line_end, d.doc_id.strip(), d.part, m.snippet[:80])
        if key in seen:
            continue
        seen.add(key)
        unique.append((d, m))

    totals = Counter(d.doc_id.strip() for d, _ in unique if d.doc_id.strip() and d.part > 0)
    requirements: list[Requirement] = []
    used: set[str] = set()
    counter = 0
    prev_group: str | None = None
    for d, m in unique:
        doc_id = d.doc_id.strip() or None
        group = doc_id if doc_id and d.part > 0 else None
        if group is None or group != prev_group:
            counter += 1
        rid = f"REQ-{counter:02d}" + (f".{d.part}" if group else "")
        while rid in used:
            counter += 1
            rid = f"REQ-{counter:02d}" + (f".{d.part}" if group else "")
        used.add(rid)
        prev_group = group
        label = doc_id or "—"
        if group and totals[doc_id] > 1:
            label = f"{doc_id} ({d.part}/{totals[doc_id]})"
        requirements.append(
            Requirement(
                rid=rid,
                doc_id=doc_id,
                doc_id_label=label,
                kind=d.kind,
                section_path=ctx.doc.section_path_of(m.line_start),
                summary=d.summary.strip() or m.snippet[:80],
                quote=m.snippet,
                locator=Locator(
                    section_path=ctx.doc.section_path_of(m.line_start),
                    line_start=m.line_start,
                    line_end=m.line_end,
                    page=ctx.doc.page_of(m.line_start),
                ),
            )
        )
    return requirements
