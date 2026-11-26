"""Трасса прогона: runs/<время>-<документ>/trace.jsonl + полные тексты запросов и ответов."""

from __future__ import annotations

import hashlib
import json
import re
import threading
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel


def _slug(text: str) -> str:
    return re.sub(r"[^\w.-]+", "-", text, flags=re.UNICODE).strip("-")[:60] or "doc"


def call_basename(n: int, step: str) -> str:
    """Имя файлов вызова в calls/ — номер и шаг. По нему ответы находит и воспроизведение прогона."""
    return f"{n:02d}-{_slug(step)}"


class RunTrace:
    def __init__(self, runs_dir: Path, doc_stem: str, started: datetime):
        base = Path(runs_dir) / f"{started.strftime('%Y%m%d-%H%M%S')}-{_slug(doc_stem)}"
        Path(runs_dir).mkdir(parents=True, exist_ok=True)
        self.dir = base
        for n in range(2, 100):  # параллельные прогоны в ту же секунду не должны делить папку
            try:
                self.dir.mkdir()
                break
            except FileExistsError:
                self.dir = base.with_name(f"{base.name}-{n}")
        (self.dir / "calls").mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._n = 0
        self.calls = 0
        self.cache_hits = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.tokens_thinking = 0
        self.errors = 0
        self.models: dict[str, int] = {}  # фактическая модель → число ответов (с учётом кэша)

    def _append(self, record: dict) -> None:
        with self._lock, open(self.dir / "trace.jsonl", "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    def event(self, name: str, **extra) -> None:
        self._append({"ts": datetime.now().isoformat(timespec="seconds"), "event": name, **extra})

    def record_call(self, req, result, latency: float, error: str | None = None) -> None:
        with self._lock:
            self._n += 1
            n = self._n
            self.calls += 1
            if error:
                self.errors += 1
            if result is not None:
                if result.model:
                    self.models[result.model] = self.models.get(result.model, 0) + 1
                if result.cache_hit:
                    self.cache_hits += 1
                else:
                    self.tokens_in += result.tokens_in
                    self.tokens_out += result.tokens_out
                    self.tokens_thinking += result.tokens_thinking
        base = self.dir / "calls" / call_basename(n, req.step)
        try:
            (base.parent / f"{base.name}.prompt.md").write_text(
                f"# step: {req.step}\n\n## system\n\n{req.system}\n\n## user\n\n{req.user}\n", encoding="utf-8"
            )
            if result is not None:
                raw = result.raw
                try:
                    raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
                except (TypeError, ValueError):
                    pass
                (base.parent / f"{base.name}.response.json").write_text(raw, encoding="utf-8")
        except OSError:
            pass
        self._append(
            {
                "n": n,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "step": req.step,
                "model": getattr(result, "model", None),
                "cache_hit": getattr(result, "cache_hit", False),
                "attempts": getattr(result, "attempts", None),
                "latency_s": round(latency, 2),
                "tokens_in": getattr(result, "tokens_in", 0),
                "tokens_out": getattr(result, "tokens_out", 0),
                "tokens_thinking": getattr(result, "tokens_thinking", 0),
                "prompt_sha256": hashlib.sha256((req.system + req.user).encode("utf-8")).hexdigest()[:16],
                "error": error,
            }
        )

    def write_json(self, name: str, obj) -> None:
        if isinstance(obj, BaseModel):
            text = obj.model_dump_json(indent=2)
        elif isinstance(obj, list) and obj and isinstance(obj[0], BaseModel):
            text = "[\n" + ",\n".join(o.model_dump_json(indent=2) for o in obj) + "\n]"
        else:
            text = json.dumps(obj, ensure_ascii=False, indent=2, default=str)
        (self.dir / name).write_text(text, encoding="utf-8")
