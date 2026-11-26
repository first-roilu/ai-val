"""Состояние одного прогона, общее для всех шагов."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from reqcheck.config import Settings
from reqcheck.grounding import Grounder
from reqcheck.ingest.structure import SourceDocument
from reqcheck.llm.base import LLMClient
from reqcheck.models import (
    Answer,
    CalcResult,
    ClaimDraft,
    Evidence,
    Fact,
    Finding,
    FindingDraft,
    GroupVerdictDraft,
    PropertyDraft,
    Question,
    ReqAssessment,
    Requirement,
    Signal,
    Stats,
    Term,
    TraceRow,
    Verdict,
)
from reqcheck.trace import RunTrace


@dataclass
class RunContext:
    settings: Settings
    doc: SourceDocument
    grounder: Grounder
    llm: LLMClient
    trace: RunTrace
    notify: Callable[[str], None]
    started: datetime
    system_prompt: str = ""
    signals: list[Signal] = field(default_factory=list)
    requirements: list[Requirement] = field(default_factory=list)
    glossary: list[Term] = field(default_factory=list)
    assessments: dict[str, ReqAssessment] = field(default_factory=dict)
    drafts: list[tuple[FindingDraft, str]] = field(default_factory=list)
    direct_findings: list[Finding] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    claims: list[tuple[ClaimDraft, Evidence | None]] = field(default_factory=list)
    calc_results: list[CalcResult] = field(default_factory=list)
    properties: dict[str, PropertyDraft] = field(default_factory=dict)
    trace_rows: list[TraceRow] = field(default_factory=list)
    group_verdicts: list[GroupVerdictDraft] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)  # ответы заказчика (--answers, --interactive)
    stats: Stats | None = None
    verdict: Verdict | None = None
    summary: str = ""
    limitations: list[str] = field(default_factory=list)
    quota_exhausted: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @property
    def offline(self) -> bool:
        return self.settings.provider == "fake"

    @property
    def req_index(self) -> dict[str, Requirement]:
        return {r.rid: r for r in self.requirements}

    def add_drafts(self, drafts: list[FindingDraft], source: str) -> None:
        with self._lock:
            self.drafts.extend((d, source) for d in drafts)

    def limit(self, message: str) -> None:
        with self._lock:
            if message not in self.limitations:
                self.limitations.append(message)
        self.trace.event("limitation", message=message)
        self.notify(f"⚠ {message}")

    def rid_range(self, rids: list[str]) -> tuple[int, int] | None:
        index = self.req_index
        found = [index[r] for r in rids if r in index]
        if not found:
            return None
        return min(r.locator.line_start for r in found), max(r.locator.line_end for r in found)

    def rids_for_lines(self, line_start: int, line_end: int, limit: int = 4) -> list[str]:
        hits = [
            r for r in self.requirements if r.locator.line_start <= line_end and line_start <= r.locator.line_end
        ]
        hits.sort(key=lambda r: abs(r.locator.line_start - line_start))
        return [r.rid for r in hits[:limit]]
