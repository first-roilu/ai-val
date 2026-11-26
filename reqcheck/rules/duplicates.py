"""Возможные дубли требований (мягкие подсказки) — точечная помощь модели по критерию «Необходимость».

Исследования показывают, что LLM почти не находят дефекты необходимости. Дубль — самый проверяемый
из них: одно правило в двух местах, и при правке одного второе останется старым.
"""

from __future__ import annotations

import re

from reqcheck.models import Category, Criterion, ReqKind, Requirement, Severity, Signal

_STOP = {"при", "если", "она", "оно", "они", "это", "для", "или", "что", "как", "все", "его", "так", "также", "быть", "только"}
_SKIP_KINDS = {ReqKind.API, ReqKind.ACCEPTANCE}


def _stems(text: str) -> set[str]:
    words = re.findall(r"[a-zа-яё0-9]+", text.lower().replace("ё", "е"))
    return {w[:5] for w in words if len(w) > 2 and w not in _STOP}


def find_duplicates(requirements: list[Requirement], limit: int = 10) -> list[Signal]:
    items = [(r, _stems(r.quote)) for r in requirements if r.kind not in _SKIP_KINDS]
    items += [(r, _stems(r.quote)) for r in requirements if r.kind == ReqKind.API and len(r.quote) >= 30]
    signals: list[Signal] = []
    seen: set[tuple[str, str]] = set()
    for i, (a, sa) in enumerate(items):
        for b, sb in items[i + 1 :]:
            if a.rid.split(".")[0] == b.rid.split(".")[0] or (a.rid, b.rid) in seen:
                continue
            small, large = (sa, sb) if len(sa) <= len(sb) else (sb, sa)
            if len(small) < 4:
                continue
            common = len(small & large)
            jaccard = common / len(sa | sb)
            containment = common / len(small)
            if jaccard >= 0.5 or (containment >= 0.8 and a.section_path != b.section_path):
                first, second = sorted((a, b), key=lambda r: r.locator.line_start)
                seen.add((a.rid, b.rid))
                signals.append(
                    Signal(
                        rule_id="R-DUPLICATE",
                        criterion=Criterion.NECESSARY,
                        category=Category.TRACE,
                        severity=Severity.MINOR,
                        hard=False,
                        line=second.locator.line_start,
                        quote=second.quote[:200],
                        message=(
                            f"Возможный дубль: {first.rid} («{first.quote[:80]}») и {second.rid} («{second.quote[:80]}») "
                            "описывают одно правило в разных местах."
                        ),
                        recommendation="Оставить правило в одном месте, во втором сослаться на него.",
                    )
                )
                if len(signals) >= limit:
                    return signals
    return signals
