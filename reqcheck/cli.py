"""Командная строка: python -m reqcheck <документ> [<отчёт>] [параметры]."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

from reqcheck import __version__
from reqcheck.config import PROVIDERS, THINKING_LEVELS, Settings
from reqcheck.errors import ReqcheckError

GO_TEXT = {"go": "можно", "conditional": "условно, после уточнений", "no_go": "нельзя"}


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m reqcheck",
        description="ИИ-агент статической проверки требований (ISO/IEC/IEEE 29148, IEEE 830): документ → отчёт на русском.",
    )
    p.add_argument("input", nargs="?", help="документ с требованиями: PDF, DOCX, MD или TXT")
    p.add_argument("output", nargs="?", help="файл отчёта (.md); рядом пишется .json. По умолчанию reports/<имя>-report.md")
    p.add_argument("-i", "--input", dest="input_flag", help="то же, что первый аргумент")
    p.add_argument("-o", "--output", dest="output_flag", help="то же, что второй аргумент")
    p.add_argument(
        "--provider",
        choices=PROVIDERS,
        help="провайдер LLM (по умолчанию LLM_PROVIDER из .env, а без него — по ключу: DeepSeek или Gemini)",
    )
    p.add_argument("--model", help="ID модели (по умолчанию GEMINI_MODEL / DEEPSEEK_MODEL / OPENAI_MODEL из .env)")
    p.add_argument(
        "--fallback",
        metavar="MODELS",
        help="резервные модели того же провайдера через запятую, на случай перегрузки основной (по умолчанию LLM_FALLBACK_MODELS)",
    )
    p.add_argument(
        "--answers",
        metavar="FILE",
        help="файл ответов заказчика (<отчёт>.answers.yaml): агент учтёт ответы и пересчитает дефекты и вердикт",
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help="после проверки задать главные уточняющие вопросы в консоли и учесть ответы в отчёте",
    )
    p.add_argument("--quick", action="store_true", help="быстрый режим: меньше вызовов LLM, без критика и проверки фактов")
    p.add_argument("--no-cache", action="store_true", help="не брать ответы модели из кэша (финальный прогон)")
    p.add_argument("--concurrency", type=int, help="число параллельных вызовов LLM")
    p.add_argument("--max-rpm", type=int, help="лимит запросов к LLM в минуту")
    p.add_argument(
        "--batch-size",
        type=int,
        help="требований в одном вызове проверки (по умолчанию 8; у Gemini подбирается — не больше 4 вызовов)",
    )
    p.add_argument("--thinking", choices=THINKING_LEVELS, help="уровень «размышлений» модели, если поддерживается")
    p.add_argument("--fail-on", choices=("critical", "major"), help="для CI: код выхода 10, если найдены дефекты этой критичности")
    p.add_argument("--version", action="version", version=f"reqcheck {__version__}")
    return p


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = _parser().parse_args(argv)
    input_path = args.input_flag or args.input
    if not input_path:
        _parser().error("укажите путь к документу с требованиями")
    output_path = args.output_flag or args.output or str(Path("reports") / f"{Path(input_path).stem}-report.md")

    from rich.console import Console

    console = Console(highlight=False)

    def notify(message: str) -> None:
        console.print(f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim] {message}")

    try:
        settings = Settings.load(
            provider=args.provider,
            model=args.model,
            fallback_models=tuple(m.strip() for m in args.fallback.split(",") if m.strip()) if args.fallback is not None else None,
            quick=True if args.quick else None,
            use_cache=False if args.no_cache else None,
            concurrency=args.concurrency,
            max_rpm=args.max_rpm,
            batch_size=args.batch_size,
            thinking=args.thinking,
        )
        settings.validate()
        reserve = settings.reserve_models
        console.print(
            f"[bold]reqcheck {__version__}[/bold] · провайдер {settings.provider} · модель {settings.model}"
            + (f" · резерв: {', '.join(reserve)}" if reserve else "")
        )
        from reqcheck.orchestrator import run

        ask_user = None
        if args.interactive:
            from reqcheck.answers import ask_in_console

            def ask_user(questions):
                return ask_in_console(questions, write=lambda text: console.print(text, markup=False), read=input)

        result = run(settings, input_path, output_path, notify, answers_file=args.answers, ask_user=ask_user)
    except ReqcheckError as exc:
        console.print(f"[bold red]Ошибка:[/bold red] {exc}")
        return exc.exit_code
    except KeyboardInterrupt:
        console.print("[yellow]Прервано пользователем.[/yellow]")
        return 130

    report = result.report
    s = report.stats.by_severity
    console.print()
    console.print(
        f"[bold]Готово.[/bold] Требований: {report.stats.requirements_total}; дефектов: {report.stats.findings_total} "
        f"(CRITICAL {s.get('CRITICAL', 0)}, MAJOR {s.get('MAJOR', 0)}, MINOR {s.get('MINOR', 0)})."
    )
    console.print(
        f"Разработка — [bold]{GO_TEXT[report.verdict.development]}[/bold]; тестирование — [bold]{GO_TEXT[report.verdict.testing]}[/bold]."
    )
    console.print(f"Отчёт: {result.markdown_path}")
    console.print(f"JSON:  {result.json_path}")
    console.print(f"Трасса: {result.run_dir}")
    if report.answers:
        resolved = report.stats.resolved_by_answers
        console.print(f"Ответы заказчика учтены: {len(report.answers)}; снято дефектов: {resolved}, частично: {report.stats.partial_by_answers}.")
    if result.answers_path is not None and report.questions:
        console.print(
            f"Вопросы заказчику: {len(report.questions)}. Ответы впишите в {result.answers_path} и запустите проверку "
            f"с флагом --answers {result.answers_path}"
        )
    if report.passport.limitations:
        console.print(f"[yellow]Ограничения прогона: {len(report.passport.limitations)} — см. приложение Б отчёта.[/yellow]")
    if result.partial:
        return 4
    if args.fail_on == "critical" and s.get("CRITICAL", 0):
        return 10
    if args.fail_on == "major" and (s.get("CRITICAL", 0) or s.get("MAJOR", 0)):
        return 10
    return 0
