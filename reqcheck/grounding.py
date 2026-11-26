"""Сверка цитат с документом: место находки вычисляет код, а не модель.

Порядок поиска: точная подстрока в нормализованном тексте → фрагменты цитаты с многоточием
по порядку → нечёткий поиск (rapidfuzz). Не нашлось — цитата считается выдуманной.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from dataclasses import dataclass

from rapidfuzz import fuzz

from reqcheck.ingest.structure import SourceDocument
from reqcheck.models import Evidence, Locator

_SPACES = set("\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u202f\u205f\u3000\t\r\n\f\v ")
_ZERO_WIDTH = set("\u200b\u200c\u200d\u2060\ufeff")
_DASHES = set("\u00ad‐‑‒–—―−﹘﹣－")
_DQUOTES = set('«»“”„‟"″〝〞＂')
_SQUOTES = set("‘’‚‛′")
_DROP = set("*`")
_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_LINE_PREFIX_RE = re.compile(r"^\s*\d{1,5}\|\s?", re.MULTILINE)
_ELLIPSIS_RE = re.compile(r"\s*(?:\.\.\.|…)\s*")
_SNIPPET_DROP_RE = re.compile(r"\*\*|__|`")

MIN_QUOTE_LEN = 4
FUZZY_MIN_LEN = 12
FUZZY_CUTOFF = 88.0


def normalize(text: str) -> tuple[str, list[int]]:
    """Нормализация для поиска + карта «позиция в нормализованном тексте → позиция в исходном»."""
    out: list[str] = []
    idx: list[int] = []

    def emit(ch: str, pos: int) -> None:
        if ch == " " and (not out or out[-1] == " "):
            return
        out.append(ch)
        idx.append(pos)

    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "<":
            m = _BR_RE.match(text, i)
            if m:
                emit(" ", i)
                i = m.end()
                continue
        if ch == "_" and i + 1 < n and text[i + 1] == "_":
            i += 2
            continue
        if ch in _DROP or ch in _ZERO_WIDTH:
            i += 1
            continue
        if ch in _SPACES or ch == "|":
            emit(" ", i)
        elif ch in _DASHES:
            emit("-", i)
        elif ch in _DQUOTES:
            emit('"', i)
        elif ch in _SQUOTES:
            emit("'", i)
        elif ch in "ёЁ":
            emit("е", i)
        else:
            for low in ch.lower():
                emit(low, i)
        i += 1
    while out and out[-1] == " ":
        out.pop()
        idx.pop()
    return "".join(out), idx


def clean_snippet(text: str, limit: int = 400) -> str:
    text = _BR_RE.sub(" ", text)
    text = _SNIPPET_DROP_RE.sub("", text)
    text = text.replace("\u00ad", "-")
    text = " ".join(text.split())
    text = text.strip(" |")
    text = re.sub(r"^[-*+]\s+", "", text)
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


@dataclass
class Match:
    line_start: int
    line_end: int
    snippet: str
    method: str
    score: float


class Grounder:
    def __init__(self, doc: SourceDocument):
        self.doc = doc
        self.text = "\n".join(line.text for line in doc.lines)
        self.norm, self.idx = normalize(self.text)
        self.line_starts: list[int] = []
        pos = 0
        for line in doc.lines:
            self.line_starts.append(pos)
            pos += len(line.text) + 1

    # ── поиск ──

    def locate(self, quote: str, near: tuple[int, int] | None = None) -> Match | None:
        q = _LINE_PREFIX_RE.sub("", quote or "").strip()
        q = q.strip(" \"'«»“”„")
        if not q:
            return None
        parts = [p for p in _ELLIPSIS_RE.split(q) if len(normalize(p)[0]) >= MIN_QUOTE_LEN]
        if len(parts) > 1:
            m = self._locate_fragments(parts, near)
            if m:
                return m
        nq, _ = normalize(q)
        if len(nq) < MIN_QUOTE_LEN:
            return None
        hits = [m.start() for m in re.finditer(re.escape(nq), self.norm)]
        if hits:
            start = self._pick(hits, near)
            return self._match(start, start + len(nq), "exact", 100.0)
        if len(nq) >= FUZZY_MIN_LEN:
            al = fuzz.partial_ratio_alignment(nq, self.norm, score_cutoff=FUZZY_CUTOFF)
            if al is not None and al.score >= FUZZY_CUTOFF and al.dest_end > al.dest_start:
                return self._match(al.dest_start, al.dest_end, "fuzzy", float(al.score))
        return None

    def _locate_fragments(self, parts: list[str], near: tuple[int, int] | None) -> Match | None:
        norm_parts = [normalize(p)[0] for p in parts]
        first_hits = [m.start() for m in re.finditer(re.escape(norm_parts[0]), self.norm)]
        if near:
            first_hits.sort(key=lambda h: self._distance(h, near))
        for start in first_hits[:10]:
            pos = start + len(norm_parts[0])
            ok = True
            for part in norm_parts[1:]:
                found = self.norm.find(part, pos, pos + 800)
                if found < 0:
                    ok = False
                    break
                pos = found + len(part)
            if ok:
                return self._match(start, pos, "fragments", 95.0)
        return None

    # ── вспомогательное ──

    def _line_of(self, orig_pos: int) -> int:
        return max(1, bisect_right(self.line_starts, orig_pos))

    def _distance(self, norm_pos: int, near: tuple[int, int]) -> int:
        line = self._line_of(self.idx[norm_pos])
        if near[0] <= line <= near[1]:
            return 0
        return min(abs(line - near[0]), abs(line - near[1]))

    def _pick(self, hits: list[int], near: tuple[int, int] | None) -> int:
        if near is None or len(hits) == 1:
            return hits[0]
        return min(hits, key=lambda h: self._distance(h, near))

    def _match(self, nstart: int, nend: int, method: str, score: float) -> Match:
        ostart = self.idx[nstart]
        oend = self.idx[nend - 1] + 1
        line_start = self._line_of(ostart)
        line_end = self._line_of(oend - 1)
        snippet = clean_snippet(self.text[ostart:oend])
        return Match(line_start, line_end, snippet, method, score)

    def locator(self, line_start: int, line_end: int) -> Locator:
        return Locator(
            section_path=self.doc.section_path_of(line_start),
            line_start=line_start,
            line_end=line_end,
            page=self.doc.page_of(line_start),
        )

    def evidence(self, quote: str, near: tuple[int, int] | None = None) -> Evidence | None:
        m = self.locate(quote, near)
        if not m:
            return None
        return Evidence(quote=m.snippet, locator=self.locator(m.line_start, m.line_end))

    def evidences(self, quotes: list[str], near: tuple[int, int] | None = None) -> tuple[list[Evidence], list[str]]:
        """Возвращает найденные цитаты (без дублей по месту) и ненайденные."""
        found: list[Evidence] = []
        missing: list[str] = []
        seen: set[tuple[int, int]] = set()
        for quote in quotes:
            ev = self.evidence(quote, near)
            if ev is None:
                if quote and quote.strip():
                    missing.append(quote)
                continue
            key = (ev.locator.line_start, ev.locator.line_end)
            if key in seen:
                continue
            seen.add(key)
            found.append(ev)
        return found, missing
