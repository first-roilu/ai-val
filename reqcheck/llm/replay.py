"""Воспроизведение прогона по трассе: ответы модели берутся из runs/<…>/calls, модель не вызывается.

Так проверяют, что отчёт сформировал агент. Промпты заново строит код агента из документа, а ответ
на каждый запрос ищется в трассе по контрольной сумме промпта. Если документ, код агента или параметры
прогона отличаются от записанных, промпт не совпадёт и воспроизведение остановится с ошибкой.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from reqcheck.errors import ConfigError, LLMError, LLMFatalError
from reqcheck.llm.base import LLMRequest, LLMResult
from reqcheck.trace import call_basename


class ReplayMismatch(LLMFatalError):
    """В трассе нет ответа на запрос: прогон с этим документом и этим кодом не записывался."""


def prompt_sha256(system: str, user: str) -> str:
    """Та же сумма, что пишет трасса в поле prompt_sha256."""
    return hashlib.sha256((system + user).encode("utf-8")).hexdigest()[:16]


def read_records(trace_dir: Path) -> list[dict]:
    path = Path(trace_dir) / "trace.jsonl"
    if not path.is_file():
        raise ConfigError(
            f"Не найдена трасса прогона: {path}. Укажите папку прогона из runs/, "
            "например runs/20261126-202502-docs-requirements."
        )
    records = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            records.append(json.loads(line))
        except ValueError:
            continue
    return records


def read_start(trace_dir: Path) -> dict:
    """Параметры записанного прогона из события start: провайдер, модель, быстрый режим, размер пачки."""
    for record in read_records(trace_dir):
        if record.get("event") == "start":
            return record
    raise ConfigError(f"В {trace_dir} нет события start: это не трасса прогона reqcheck.")


def _summary(trace_dir: Path) -> dict:
    path = Path(trace_dir) / "summary.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def recorded_report(trace_dir: Path) -> Path | None:
    """Файл отчёта записанного прогона (путь из summary.json, от корня проекта)."""
    path = str(_summary(trace_dir).get("report") or "").replace("\\", "/")
    return Path(path) if path else None


class ReplayProvider:
    """Провайдер, который отвечает записанными ответами модели. Ключ и сеть не нужны."""

    def __init__(self, trace_dir: Path):
        self.dir = Path(trace_dir)
        records = read_records(self.dir)
        start = next((r for r in records if r.get("event") == "start"), {})
        self.name = "replay"
        self.model = start.get("model") or ""
        self._calls: dict[str, dict] = {}
        for record in records:
            key = record.get("prompt_sha256")
            if "n" not in record or not key:
                continue
            known = self._calls.get(key)
            if known is None or known.get("error") or not record.get("error"):  # ответ важнее ошибки
                self._calls[key] = record

    def generate(self, req: LLMRequest) -> LLMResult:
        record = self._calls.get(prompt_sha256(req.system, req.user))
        if record is None:
            raise ReplayMismatch(
                f"В трассе нет ответа на вызов «{req.step}»: документ, код агента или параметры прогона "
                "отличаются от записанных."
            )
        if record.get("error"):  # в записанном прогоне шаг не выполнился — воспроизводим так же
            raise LLMError(f"в записанном прогоне вызов завершился ошибкой: {record['error']}")
        path = self.dir / "calls" / f"{call_basename(record['n'], record['step'])}.response.json"
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ReplayMismatch(f"В трассе нет файла ответа {path.name}.") from exc
        return LLMResult(raw=raw, model=record.get("model") or self.model, cache_hit=True)


def _body(text: str) -> str:
    """Отчёт без шапки и паспорта: в них время, длительность и способ прогона."""
    text = text.replace("\r\n", "\n")
    start = text.find("\n## 2.")
    return text[start:] if start >= 0 else text


def compare_with_recorded(trace_dir: Path, report, md_path: Path) -> tuple[list[str], str]:
    """Сверяет воспроизведённый отчёт с записанным прогоном.

    Итоги (число находок, критичность, вердикт) сверяются с summary.json трассы, текст отчёта — с файлом
    записанного отчёта, если он на месте. Возвращает расхождения и путь отчёта, с которым совпал текст.
    """
    summary = _summary(trace_dir)
    if not summary:
        return ["в трассе нет summary.json: записанный прогон не завершился"], ""
    got = {
        "findings": len(report.findings),
        "by_severity": report.stats.by_severity,
        "verdict": {"development": report.verdict.development, "testing": report.verdict.testing},
    }
    got = json.loads(json.dumps(got, default=str))
    problems = [
        f"{label}: {got[key]} вместо {summary.get(key)}"
        for key, label in (("findings", "число находок"), ("by_severity", "критичность"), ("verdict", "вердикт"))
        if got[key] != summary.get(key)
    ]
    reference = ""
    recorded = recorded_report(trace_dir)
    if recorded is not None and recorded.is_file() and recorded.resolve() != Path(md_path).resolve():
        if _body(recorded.read_text(encoding="utf-8")) == _body(Path(md_path).read_text(encoding="utf-8")):
            reference = recorded.as_posix()
        else:
            problems.append(f"текст отчёта отличается от {recorded.as_posix()}")
    return problems, reference
