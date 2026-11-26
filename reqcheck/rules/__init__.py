"""Детерминированные проверки документа."""

from __future__ import annotations

from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Signal
from reqcheck.rules import api_contract, doc_meta, identifiers, lexicon, placeholders, text_integrity, units

RULES = (text_integrity, placeholders, identifiers, lexicon, api_contract, units, doc_meta)


def run_rules(doc: SourceDocument) -> tuple[list[Signal], list[str]]:
    signals: list[Signal] = []
    problems: list[str] = []
    for rule in RULES:
        try:
            signals.extend(rule.check(doc))
        except Exception as exc:  # правило не должно ронять прогон
            problems.append(f"Правило {rule.__name__.rsplit('.', 1)[-1]} не выполнено: {type(exc).__name__}: {exc}")
    seen: set[tuple[str, int, str]] = set()
    unique: list[Signal] = []
    for s in sorted(signals, key=lambda s: (s.line, s.rule_id)):
        key = (s.rule_id, s.line, s.message)
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique, problems
