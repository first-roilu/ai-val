"""Оркестратор: порядок шагов, параллельные вызовы, деградация при сбоях."""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path

from reqcheck import prompts
from reqcheck.answers import load_answers, write_template
from reqcheck.config import Settings, review_batch_size
from reqcheck.context import RunContext
from reqcheck.errors import LLMError, LLMFatalError, LLMQuotaExhausted
from reqcheck.grounding import Grounder
from reqcheck.ingest import load_document
from reqcheck.llm import build_client
from reqcheck.llm.fake import OFFLINE_NOTE
from reqcheck.models import INDIVIDUAL_CRITERIA, Answer, CriterionCheck, Question, ReqAssessment, Report, Severity
from reqcheck.report import build_report, write_outputs
from reqcheck.rules import run_rules
from reqcheck.steps import clarify, critic, extract, facts, lenses, merge, review, synthesis
from reqcheck.trace import RunTrace


@dataclass
class RunResult:
    report: Report
    markdown_path: Path
    json_path: Path
    run_dir: Path
    partial: bool
    answers_path: Path | None = None


def _guarded(ctx: RunContext, label: str, fn: Callable[[], None]) -> None:
    if ctx.quota_exhausted:
        ctx.limit(f"Шаг «{label}» пропущен: квота LLM исчерпана.")
        return
    try:
        fn()
    except LLMFatalError:
        raise
    except LLMQuotaExhausted as exc:
        ctx.quota_exhausted = True
        ctx.limit(f"Шаг «{label}» не выполнен: {exc}")
    except LLMError as exc:
        ctx.limit(f"Шаг «{label}» не выполнен: {exc}")
    except Exception as exc:  # шаг не должен ронять весь прогон
        ctx.trace.event("step_error", step=label, error=f"{type(exc).__name__}: {exc}", traceback=traceback.format_exc())
        ctx.limit(f"Шаг «{label}» завершился ошибкой: {type(exc).__name__}: {exc}")


def _run_parallel(ctx: RunContext, tasks: list[tuple[str, Callable[[], None]]]) -> None:
    if not tasks:
        return
    workers = max(1, min(ctx.settings.concurrency, len(tasks)))
    if workers == 1:
        for label, fn in tasks:
            _guarded(ctx, label, fn)
        return
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(_guarded, ctx, label, fn) for label, fn in tasks]
        for future in futures:
            future.result()  # пробрасывает LLMFatalError


AskUser = Callable[[list[Question]], list[Answer]]


def run(
    settings: Settings,
    input_path: str | Path,
    output_path: str | Path,
    notify: Callable[[str], None],
    answers_file: str | Path | None = None,
    ask_user: AskUser | None = None,
) -> RunResult:
    started = datetime.now().astimezone()
    doc = load_document(input_path)
    trace = RunTrace(settings.runs_dir, _run_name(Path(input_path)), started)
    llm = build_client(settings, trace, notify)
    ctx = RunContext(
        settings=settings,
        doc=doc,
        grounder=Grounder(doc),
        llm=llm,
        trace=trace,
        notify=notify,
        started=started,
        system_prompt=prompts.render("system.md.j2"),
    )
    ctx.limitations.extend(doc.limitations)
    answers: list[Answer] = []
    if answers_file:
        answers, answers_sha = load_answers(answers_file)
        ctx.answers = answers  # сохраняются в файле ответов, даже если учесть их не удастся
        notify(f"Ответы заказчика: {len(answers)} из файла {answers_file}")
        # файлы ответов версии 1.0.0 хранят сумму байтов файла — на Windows с переводами строк CRLF
        if answers_sha and answers_sha not in (doc.sha256, hashlib.sha256(Path(input_path).read_bytes()).hexdigest()):
            ctx.limit(
                "Ответы заказчика даны к другой версии документа (контрольная сумма не совпадает): "
                "проверьте, что вопросы ещё актуальны."
            )
    trace.event(
        "start",
        document=doc.name,
        sha256=doc.sha256,
        provider=settings.provider,
        model=llm.model,
        lines=len(doc.lines),
        quick=settings.quick,  # режим и размер пачки меняют промпты: по ним настраивается воспроизведение
        batch_size=settings.batch_size,
        **({"replay": settings.replay_dir.as_posix()} if settings.replay_dir is not None else {}),
    )
    notify(f"Документ: {doc.name} ({doc.fmt.upper()}, строк: {len(doc.lines)}, разделов: {len(doc.sections) - 1})")
    if ctx.offline:
        ctx.limit(OFFLINE_NOTE + " Показаны только детерминированные проверки.")

    # 2. правила
    ctx.signals, problems = run_rules(doc)
    for problem in problems:
        ctx.limit(problem)
    trace.write_json("signals.json", ctx.signals)
    notify(f"Правила: сигналов {len(ctx.signals)} (жёстких {sum(s.hard for s in ctx.signals)})")

    # 3. реестр требований — без него дальше идти некуда
    extract.run(ctx)
    notify(f"Реестр: требований {len(ctx.requirements)}")
    # без требований смысловая проверка бессмысленна: шаги модели пропускаются, вердикт «нельзя» ставит код
    has_requirements = bool(ctx.requirements)
    if not has_requirements:
        ctx.limit("В документе не найдено ни одного требования: смысловая проверка не выполнялась.")

    # 4–5. ревью требований и линзы, не зависящие от фактов
    quick = settings.quick
    batch_size = review_batch_size(settings, len(ctx.requirements))
    batches = review.make_batches(ctx.requirements, batch_size)
    tasks: list[tuple[str, Callable[[], None]]] = [
        (f"Проверка требований {i}/{len(batches)}", partial(review.run_batch, ctx, batch, str(i)))
        for i, batch in enumerate(batches, start=1)
    ]
    if has_requirements:
        if not quick:
            tasks.append(("Факты и расчёты", partial(facts.run, ctx)))
        tasks.append(("Линза полноты и трассировки", partial(lenses.completeness, ctx)))
        tasks.append(("Линза стандартов и рисков", partial(lenses.standards, ctx)))
    _run_parallel(ctx, tasks)
    for r in ctx.requirements:
        if r.rid not in ctx.assessments:
            ctx.assessments[r.rid] = ReqAssessment(
                rid=r.rid,
                checks=[CriterionCheck(criterion=c, status="na", note="не оценено") for c in INDIVIDUAL_CRITERIA],
                conclusion="Требование не проверено моделью: шаг проверки не выполнен.",
                reviewed=False,
            )

    # 5a, 5d, 5e — после фактов
    facts.run_calc(ctx, ctx.claims)
    tasks = []
    if not quick and has_requirements:
        tasks.append(("Линза непротиворечивости", partial(lenses.consistency, ctx)))
        if lenses.needs_business(ctx):
            tasks.append(("Линза бизнес-обоснования", partial(lenses.business, ctx)))
    _run_parallel(ctx, tasks)

    # 6. слияние
    merge.run(ctx)
    notify(f"Находок после слияния: {len(ctx.findings)}")

    # 7. критик
    if not quick and not ctx.offline and has_requirements:
        _guarded(ctx, "Критик", lambda: critic.run(ctx))
        notify(f"Находок после критика: {len(ctx.findings)}")

    # 7a. ответы заказчика из файла
    if answers and ctx.offline:
        ctx.limit("Ответы заказчика не учтены: в офлайн-режиме нет модели, которая их разберёт.")
    elif answers and has_requirements:
        _guarded(ctx, "Учёт ответов заказчика", lambda: clarify.run(ctx, answers))
        _notify_answers(ctx)

    # 8. итог
    if has_requirements:
        synthesis.run(ctx)
    else:
        synthesis.run_without_requirements(ctx)

    # 9. отчёт
    report = build_report(ctx)
    md_path, json_path = write_outputs(report, output_path)

    # 9a. уточнения в консоли: ответы → пересчёт дефектов и вердикта → отчёт заново
    if ask_user is not None and ctx.questions and not ctx.offline:
        new_answers = ask_user(ctx.questions)
        if new_answers:
            known = {a.question for a in new_answers}
            combined = [a for a in ctx.answers if a.question not in known] + new_answers
            _guarded(ctx, "Учёт ответов заказчика", lambda: clarify.run(ctx, combined))
            _notify_answers(ctx)
            synthesis.run(ctx)
            report = build_report(ctx)
            md_path, json_path = write_outputs(report, output_path)
    answers_path = write_template(report, md_path, str(input_path)) if (report.questions or report.answers) else None
    trace.write_json("summary.json", {
        "document": doc.name,
        "findings": len(report.findings),
        "by_severity": report.stats.by_severity,
        "verdict": {"development": report.verdict.development, "testing": report.verdict.testing},
        "report": str(md_path),
        "llm_calls": trace.calls,
        "cache_hits": trace.cache_hits,
        "limitations": ctx.limitations,
    })
    trace.event("finish", findings=len(report.findings), critical=report.stats.by_severity.get(Severity.CRITICAL.value, 0))
    return RunResult(report, md_path, json_path, trace.dir, partial=ctx.quota_exhausted, answers_path=answers_path)


def _run_name(path: Path) -> str:
    """Имя папки трассы: для типовых имён файла («spec», «tz») добавляем имя папки документа."""
    generic = {"spec", "tz", "тз", "requirements", "document", "doc", "srs", "brd"}
    if path.stem.lower() in generic and path.parent.name:
        return f"{path.parent.name}-{path.stem}"
    return path.stem


def _notify_answers(ctx: RunContext) -> None:
    resolved = sum(1 for f in ctx.findings if f.resolution == "resolved")
    partial = sum(1 for f in ctx.findings if f.resolution == "partial")
    ctx.notify(f"Ответы учтены: снято дефектов {resolved}, частично {partial}")
