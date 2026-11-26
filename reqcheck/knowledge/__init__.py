"""База знаний: критерии, рубрика, словарь размытых формулировок, чек-листы предметной области."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

KNOWLEDGE_DIR = Path(__file__).resolve().parent


def _load(name: str):
    return yaml.safe_load((KNOWLEDGE_DIR / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_criteria() -> dict:
    return _load("criteria.yaml")


@lru_cache(maxsize=1)
def load_severity() -> dict:
    return _load("severity.yaml")


@lru_cache(maxsize=1)
def load_lexicon() -> list[dict]:
    entries = _load("lexicon.yaml")
    for entry in entries:
        entry["re"] = re.compile(entry["pattern"], re.IGNORECASE)
    return entries


@lru_cache(maxsize=1)
def load_checklists() -> list[dict]:
    lists = _load("checklists.yaml")
    for cl in lists:
        cl["re"] = [re.compile(t, re.IGNORECASE) for t in cl.get("triggers", [])]
    return lists


def select_checklists(text: str, kinds: set[str], limit: int = 5) -> list[dict]:
    scored = []
    for cl in load_checklists():
        score = 3 * len(kinds & set(cl.get("kinds", [])))
        score += sum(1 for rx in cl["re"] if rx.search(text))
        if score > 0:
            scored.append((score, cl))
    scored.sort(key=lambda x: -x[0])
    return [cl for _, cl in scored[:limit]]


def render_criteria(scope: str = "individual") -> str:
    crit = load_criteria()[scope]
    lines = []
    for code, c in crit.items():
        lines.append(f"- **{code} — {c['name']}** ({c['standard']}): {c['definition']}")
        for q in c.get("check", []):
            lines.append(f"  - проверь: {q}")
        for d in c.get("defects", []):
            lines.append(f"  - типовой дефект: {d}")
    return "\n".join(lines)


def render_severity() -> str:
    lines = []
    for level, spec in load_severity().items():
        lines.append(f"- **{level}** — {spec['consequence']}")
        lines.extend(f"  - {w}" for w in spec["when"])
    return "\n".join(lines)


def render_checklists(lists: list[dict]) -> str:
    if not lists:
        return "(нет подходящих чек-листов)"
    blocks = []
    for cl in lists:
        blocks.append(f"**{cl['title']}**\n" + "\n".join(f"- {item}" for item in cl["items"]))
    return "\n\n".join(blocks)
