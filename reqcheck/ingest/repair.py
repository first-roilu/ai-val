"""Восстановление повреждённого текста: «кракозябры», невидимые символы, смешанные алфавиты.

Строки чинятся по одной, их число не меняется — номера строк и места цитат остаются верными.
Что починить нельзя (�, «???», символы из частной области Unicode, «(cid:12)»), не трогаем
и отдаём правилу R-BROKEN-TEXT: такие места попадают в отчёт как дефект понятности.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

try:  # ftfy надёжно чинит UTF-8, прочитанный как cp1251/latin-1 («РўСЂРµ…», «Ð¢Ñ…»)
    import ftfy
except ImportError:  # pragma: no cover
    ftfy = None

# частоты букв русского языка, %
_FREQ = {
    "о": 10.97, "е": 8.45, "а": 8.01, "и": 7.35, "н": 6.70, "т": 6.26, "с": 5.47, "р": 4.73, "в": 4.54,
    "л": 4.40, "к": 3.49, "м": 3.21, "д": 2.98, "п": 2.81, "у": 2.62, "я": 2.01, "ы": 1.90, "ь": 1.74,
    "г": 1.70, "з": 1.65, "б": 1.59, "ч": 1.44, "й": 1.21, "х": 0.97, "ж": 0.94, "ш": 0.73, "ю": 0.64,
    "ц": 0.48, "щ": 0.36, "э": 0.32, "ф": 0.26, "ъ": 0.04, "ё": 0.04,
}
_LOGP = {k: math.log(v / 100) for k, v in _FREQ.items()}
_KAZAKH = set("әғқңөұүһі")
_CYR_RE = re.compile(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]")
_LETTER_RE = re.compile(r"[^\W\d_]")
# однобайтовые путаницы: (как ошибочно прочитали, как надо читать)
_SINGLE_BYTE = [("latin-1", "cp1251"), ("cp1252", "cp1251"), ("cp1250", "cp1251"), ("cp866", "cp1251"), ("cp1251", "cp866"), ("koi8-r", "cp1251"), ("cp1251", "koi8-r")]
_SUSPICIOUS_RE = re.compile(r"[À-ÿ]{2}|[─-╿]|[РС][Ђ-Џђ-џ\u00a1-\u00bf‘-›€№™]|[ÐÑ][\u0080-¿]")
_INVISIBLE = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff"), None)
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl", "ﬅ": "st", "ﬆ": "st"}
# смешанные алфавиты: латинские двойники кириллических букв и наоборот
_LAT2CYR = str.maketrans("AaBCcEeHKkMOoPpTXxYy", "АаВСсЕеНКкМОоРрТХхУу")
_CYR2LAT = str.maketrans("АаВСсЕеНКкМОоРрТХхУу", "AaBCcEeHKkMOoPpTXxYy")
_WORD_RE = re.compile(r"[^\W\d_]{3,}")
# неисправимые повреждения
_BROKEN_PATTERNS = [
    ("replacement", re.compile(r"�+")),
    ("private_use", re.compile(r"[\ue000-\uf8ff]+")),
    ("cid", re.compile(r"\(cid:\d+\)")),
    ("question_marks", re.compile(r"[А-Яа-яЁё]\?+[А-Яа-яЁё]|(?:\?{3,}[\s,.;:]*){2,}")),
]
BROKEN_LABEL = {
    "replacement": "символы-заменители «�» — исходные буквы потеряны",
    "private_use": "служебные символы шрифта вместо букв (частная область Unicode)",
    "cid": "коды глифов «(cid:N)» вместо букв — текстовый слой PDF без таблицы символов",
    "question_marks": "буквы заменены знаками «?» — потеря при перекодировке",
}


@dataclass
class RepairReport:
    fixed: dict[str, list[int]] = field(default_factory=dict)  # вид починки → номера строк
    examples: list[tuple[int, str, str]] = field(default_factory=list)  # (строка, было, стало)
    broken: list[tuple[int, str, str]] = field(default_factory=list)  # (строка, вид, фрагмент)
    invisible_removed: int = 0

    def add_fix(self, kind: str, line_no: int, before: str, after: str) -> None:
        self.fixed.setdefault(kind, []).append(line_no)
        if len(self.examples) < 12:
            self.examples.append((line_no, before.strip()[:120], after.strip()[:120]))

    @property
    def fixed_lines(self) -> int:
        return len({n for lines in self.fixed.values() for n in lines})

    @property
    def has_changes(self) -> bool:
        return bool(self.fixed or self.broken or self.invisible_removed)


FIX_LABEL = {
    "encoding": "испорченная кодировка («кракозябры»)",
    "mixed_script": "латинские буквы внутри русских слов и наоборот",
    "ligature": "типографские лигатуры",
    "control": "управляющие символы",
}


def russian_score(text: str) -> float:
    """Средний логарифм частоты кириллических букв: русский текст ≈ −3, «кракозябры» ≈ −5 и ниже."""
    letters = [ch for ch in text.lower() if "а" <= ch <= "я" or ch == "ё" or ch in _KAZAKH]
    if not letters:
        return -10.0
    return sum(_LOGP.get(ch, math.log(0.005)) for ch in letters) / len(letters)


_UPPER_RE = re.compile(r"[А-ЯЁ]")


def _quality(text: str) -> float:
    letters = _LETTER_RE.findall(text)
    if not letters:
        return 0.0
    cyr_count = len(_CYR_RE.findall(text))
    cyr = cyr_count / len(letters)
    junk = len(re.findall(r"[À-ÿ─-╿�]", text)) / max(len(text), 1)
    # KOI8-R ↔ cp1251 переворачивает регистр: «фТЕВПЧБОЙС» — почти все буквы заглавные
    upper = len(_UPPER_RE.findall(text)) / max(cyr_count, 1)
    return cyr * 2 + max(russian_score(text), -8) / 3 - junk * 5 - upper * 2


def _try_single_byte(line: str) -> str | None:
    best, best_q = None, _quality(line)
    for wrong, right in _SINGLE_BYTE:
        try:
            candidate = line.encode(wrong).decode(right)
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        q = _quality(candidate)
        if q > best_q + 0.8 and russian_score(candidate) > -3.6:
            best, best_q = candidate, q
    return best


def _fix_mixed_script(line: str) -> tuple[str, int]:
    count = 0

    def fix(m: re.Match) -> str:
        nonlocal count
        word = m.group(0)
        cyr = len(_CYR_RE.findall(word))
        lat = len(re.findall(r"[A-Za-z]", word))
        if not cyr or not lat:
            return word
        if cyr > lat:
            fixed = word.translate(_LAT2CYR)
        elif lat > cyr:
            fixed = word.translate(_CYR2LAT)
        else:
            return word
        if fixed != word and not (_CYR_RE.search(fixed) and re.search(r"[A-Za-z]", fixed)):
            count += 1
            return fixed
        return word

    return _WORD_RE.sub(fix, line), count


def repair_line(line: str, no: int, report: RepairReport, *, pdf: bool = False) -> str:
    original = line
    if pdf:
        line = line.replace("\u00ad", "-")  # в PDF мягкий перенос — это извлечённый дефис
    else:
        line = line.replace("\u00ad", "")
    stripped = line.translate(_INVISIBLE)
    report.invisible_removed += len(line) - len(stripped)
    line = stripped

    # сначала кодировка: в «кракозябрах» из latin-1 символы U+0080–U+009F — это байты исходного текста
    if _SUSPICIOUS_RE.search(line):
        before = line
        fixed = ftfy.fix_encoding(line) if ftfy is not None else line
        if fixed != line and _quality(fixed) > _quality(line):
            line = fixed
        else:
            candidate = _try_single_byte(line)
            if candidate is not None:
                line = candidate
        if line != before:
            report.add_fix("encoding", no, before, line)

    if _CONTROL_RE.search(line):
        before = line
        line = _CONTROL_RE.sub("", line)
        report.add_fix("control", no, before, line)
    if any(ch in line for ch in _LIGATURES):
        before = line
        for lig, repl in _LIGATURES.items():
            line = line.replace(lig, repl)
        report.add_fix("ligature", no, before, line)

    before = line
    line, mixed = _fix_mixed_script(line)
    if mixed:
        report.add_fix("mixed_script", no, before, line)

    for kind, rx in _BROKEN_PATTERNS:
        m = rx.search(line)
        if m:
            start = max(0, m.start() - 40)
            report.broken.append((no, kind, line[start : m.end() + 40].strip()))
            break
    return line


def repair_lines(lines: list[tuple[str, int | None]], *, pdf: bool = False) -> tuple[list[tuple[str, int | None]], RepairReport]:
    report = RepairReport()
    out = [(repair_line(text, no, report, pdf=pdf), page) for no, (text, page) in enumerate(lines, start=1)]
    return out, report


def decode_bytes(data: bytes) -> tuple[str, str]:
    """Декодирует текстовый файл: BOM → UTF-8 → лучшая по «русскости» однобайтовая кодировка."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16"), "utf-16"
    try:
        return data.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        pass
    # cp1251 — по умолчанию; другая кодировка должна быть заметно «русскее»
    candidates = {}
    for enc in ("cp1251", "koi8-r", "cp866"):
        try:
            candidates[enc] = data.decode(enc)
        except UnicodeDecodeError:
            continue
    if not candidates:
        return data.decode("utf-8", errors="replace"), "utf-8 (с заменой)"
    scores = {enc: _quality(text[:20000]) for enc, text in candidates.items()}
    best_enc = "cp1251" if "cp1251" in scores else max(scores, key=scores.get)
    for enc, q in scores.items():
        if q > scores[best_enc] + 0.3:
            best_enc = enc
    return candidates[best_enc], best_enc
