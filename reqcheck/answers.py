"""Цикл уточнений: файл ответов рядом с отчётом, загрузка ответов и вопросы в консоли.

1. Каждый прогон пишет рядом с отчётом `<отчёт>.answers.yaml`: вопросы, варианты и пустые ответы.
2. Заказчик вписывает номер варианта или свой ответ.
3. Прогон с `--answers <файл>` учитывает ответы: шаг clarify решает, какие дефекты сняты,
   предлагает правки документа, вердикт пересчитывается. В `--interactive` то же самое в консоли.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import yaml

from reqcheck.errors import InputError
from reqcheck.models import Answer, Question, Report

HEADER = """# Ответы на уточняющие вопросы reqcheck.
# В answer впишите номер варианта (1, 2, …) или свой ответ текстом. Пустой answer — вопрос пропущен.
# Затем запустите проверку с ответами:
#   python -m reqcheck {document} {report} --answers {answers}
"""

QUIT = {"q", "й", "quit", "exit", "выход"}


def answers_path(md_path: Path) -> Path:
    return md_path.with_name(md_path.stem + ".answers.yaml")


def _option_map(options) -> dict[str, str]:
    if isinstance(options, dict):
        return {str(k).strip(): str(v).strip() for k, v in options.items() if str(v).strip()}
    if isinstance(options, list):
        return {str(i): str(v).strip() for i, v in enumerate(options, start=1) if str(v).strip()}
    return {}


def resolve_reply(reply: str, options: list[str]) -> str:
    """Номер варианта → «Вариант N: текст»; иначе ответ как есть."""
    text = reply.strip()
    opts = _option_map(options)
    if text in opts:
        return f"Вариант {text}: {opts[text]}"
    return text


def load_answers(path: str | Path) -> tuple[list[Answer], str]:
    """Читает файл ответов. Возвращает ответы (только заполненные) и sha256 документа из файла."""
    p = Path(path)
    if not p.is_file():
        raise InputError(f"Файл ответов не найден: {p}")
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise InputError(f"Файл ответов {p} не читается как YAML: {exc}") from exc
    items = data.get("questions") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise InputError(f"В файле ответов {p} нет списка questions")
    answers: list[Answer] = []
    for n, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        raw = item.get("answer")
        if raw is None or not str(raw).strip():
            continue
        options = list(_option_map(item.get("options")).values())
        answers.append(
            Answer(
                qid=str(item.get("id") or f"Q-{n:02d}").strip(),
                question=str(item.get("question") or "").strip(),
                answer=resolve_reply(str(raw), item.get("options") or []),
                options=options,
                finding_ids=[str(x).strip() for x in (item.get("findings") or []) if str(x).strip()],
            )
        )
    return answers, str(data.get("sha256") or "") if isinstance(data, dict) else ""


def write_template(report: Report, md_path: Path, document_arg: str) -> Path:
    """Файл ответов: уже полученные ответы сохраняются, новые вопросы — с пустым answer."""
    path = answers_path(md_path)
    items = []
    for a in report.answers:
        item = {"id": a.qid, "question": a.question}
        if a.options:
            item["options"] = {i: o for i, o in enumerate(a.options, start=1)}
        if a.finding_ids:
            item["findings"] = a.finding_ids
        item["answer"] = a.answer
        items.append(item)
    answered = {a.question for a in report.answers}
    for q in report.questions:
        if q.text in answered:
            continue
        item = {"id": q.qid, "priority": q.priority, "addressee": q.addressee, "question": q.text}
        if q.options:
            item["options"] = {i: o for i, o in enumerate(q.options, start=1)}
        if q.recommended:
            item["recommended"] = q.recommended
        if q.finding_ids:
            item["findings"] = q.finding_ids
        item["answer"] = ""
        items.append(item)
    if not items:
        return path
    body = yaml.safe_dump(
        {"document": report.passport.document, "sha256": report.passport.sha256, "questions": items},
        allow_unicode=True,
        sort_keys=False,
        width=110,
    )
    header = HEADER.format(
        document=document_arg.replace("\\", "/"),
        report=str(md_path).replace("\\", "/"),
        answers=str(path).replace("\\", "/"),
    )
    path.write_text(header + body, encoding="utf-8")
    return path


def ask_in_console(
    questions: list[Question],
    write: Callable[[str], None],
    read: Callable[[str], str] = input,
    limit: int = 10,
) -> list[Answer]:
    """Задаёт главные вопросы в консоли. Enter — пропустить, q — закончить."""
    picked = [q for q in questions if q.priority <= 2][:limit] or questions[:limit]
    if not picked:
        return []
    write(
        f"\nУточняющие вопросы: {len(picked)} из {len(questions)}. Введите номер варианта или свой ответ; "
        "Enter — пропустить, q — закончить."
    )
    answers: list[Answer] = []
    for q in picked:
        write(f"\n{q.qid} · {q.addressee} · приоритет {q.priority}\n{q.text}")
        for i, option in enumerate(q.options, start=1):
            write(f"  {i}. {option}")
        if q.recommended:
            write(f"  Рекомендация ревью: {q.recommended}")
        try:
            reply = read("> ").strip()
        except EOFError:
            break
        if reply.lower() in QUIT:
            break
        if not reply:
            continue
        answers.append(
            Answer(
                qid=q.qid,
                question=q.text,
                answer=resolve_reply(reply, q.options),
                options=list(q.options),
                finding_ids=list(q.finding_ids),
            )
        )
    return answers
