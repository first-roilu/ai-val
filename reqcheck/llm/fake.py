"""Офлайн-провайдер: валидные по схеме ответы без обращения к модели.

Нужен для тестов и для проверки конвейера без ключа: нарезку делает эвристика,
смысловые проверки не выполняются (в отчёте это явно отмечено), работают правила и калькулятор.
"""

from __future__ import annotations

import re

from reqcheck.llm.base import LLMRequest, LLMResult
from reqcheck.models import (
    BusinessOut,
    CheckDraft,
    CompletenessOut,
    ConsistencyOut,
    CriticOut,
    AssessmentDraft,
    ExtractFixOut,
    ExtractOut,
    FactsOut,
    INDIVIDUAL_CRITERIA,
    PropertyDraft,
    ReqDraft,
    ReqKind,
    ReviewOut,
    StandardsOut,
    SynthesisOut,
    Criterion,
)

OFFLINE_NOTE = "Офлайн-режим (LLM_PROVIDER=fake): смысловая проверка моделью не выполнялась."

_ID_RE = re.compile(r"^\W*([A-Za-zА-ЯЁа-яё]{1,6}-\d{1,3}(?:\.\d{1,3})*)\.?")
_KIND_BY_SECTION = [
    (r"критери|приемк|приёмк|acceptance", ReqKind.ACCEPTANCE),
    (r"нефункц|nfr|производит|доступност", ReqKind.NFR),
    (r"данн", ReqKind.DATA),
    (r"интеграц", ReqKind.INTEGRATION),
    (r"api|rest|параметр|статус|ответ|логика работы|особые правила|запрос", ReqKind.API),
    (r"ui|интерфейс|дизайн|экран", ReqKind.UI),
    (r"bpmn|процесс|sequence|последовательн", ReqKind.PROCESS),
    (r"архитект", ReqKind.ARCHITECTURE),
    (r"цел|бизнес", ReqKind.BUSINESS),
    (r"функцион", ReqKind.FUNCTIONAL),
]
_SKIP_SECTIONS = re.compile(r"общие сведения|ревью|согласующ|оглавлен|история изменений|общие параметры", re.IGNORECASE)


def _kind(section: str) -> ReqKind:
    low = section.lower()
    for pattern, kind in _KIND_BY_SECTION:
        if re.search(pattern, low):
            return kind
    return ReqKind.FUNCTIONAL


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|`|<br\s*/?>", "", text)
    text = re.sub(r"^\s*(?:[-*+]|\d+\.)\s+", "", text)
    return " ".join(text.split())


class FakeProvider:
    name = "fake"
    model = "fake-heuristic"

    def generate(self, req: LLMRequest) -> LLMResult:
        schema = req.schema
        if schema is ExtractOut:
            data = self._extract(req)
        elif schema is ExtractFixOut:
            data = ExtractFixOut(requirements=[])
        elif schema is ReviewOut:
            data = ReviewOut(
                assessments=[
                    AssessmentDraft(
                        rid=rid,
                        checks=[CheckDraft(criterion=c, status="na", note="") for c in INDIVIDUAL_CRITERIA],
                        conclusion=OFFLINE_NOTE,
                    )
                    for rid in req.meta.get("rids", [])
                ]
            )
        elif schema is FactsOut:
            data = FactsOut()
        elif schema is ConsistencyOut:
            data = ConsistencyOut(properties=[PropertyDraft(criterion=Criterion.SET_CONSISTENT, status="warn", summary=OFFLINE_NOTE)])
        elif schema is CompletenessOut:
            data = CompletenessOut(
                properties=[
                    PropertyDraft(criterion=c, status="warn", summary=OFFLINE_NOTE)
                    for c in (Criterion.SET_COMPLETE, Criterion.SET_TRACEABLE, Criterion.SET_VALIDATABLE)
                ]
            )
        elif schema is StandardsOut:
            data = StandardsOut(properties=[PropertyDraft(criterion=Criterion.SET_FEASIBLE, status="warn", summary=OFFLINE_NOTE)])
        elif schema is BusinessOut:
            data = BusinessOut()
        elif schema is CriticOut:
            data = CriticOut()
        elif schema is SynthesisOut:
            data = SynthesisOut(
                summary=OFFLINE_NOTE + " В отчёте — только результаты детерминированных проверок (правила, расчёты, структура).",
                rationale_dev="Вердикт рассчитан по находкам правил; смысловые дефекты не искались.",
                rationale_test="Вердикт рассчитан по находкам правил; смысловые дефекты не искались.",
            )
        else:  # pragma: no cover
            raise ValueError(f"FakeProvider не знает схему {schema.__name__}")
        return LLMResult(raw=data.model_dump_json(), model=self.model)

    def _extract(self, req: LLMRequest) -> ExtractOut:
        drafts: list[ReqDraft] = []
        for _, text, section in req.meta.get("lines", []):
            if not text.strip() or _SKIP_SECTIONS.search(section):
                continue
            stripped = text.strip()
            if stripped.startswith("#") or set(stripped) <= set("|-: "):
                continue
            is_item = bool(re.match(r"^\s*(?:[-*+]|\d+\.)\s+", text))
            is_row = stripped.startswith("|") and stripped.count("|") >= 3
            clean = _clean(stripped.strip("|").replace("|", " "))
            if len(clean.split()) < 3:
                continue
            if not (is_item or is_row or len(clean.split()) >= 6):
                continue
            if is_row and re.search(r"исходные условия|parameter|поле \| значение", clean, re.IGNORECASE):
                continue
            m = _ID_RE.match(_clean(stripped))
            quote = clean if len(clean) <= 200 else clean[:200].rsplit(" ", 1)[0]
            drafts.append(
                ReqDraft(
                    doc_id=m.group(1) if m else "",
                    part=0,
                    kind=_kind(section),
                    summary=" ".join(clean.split()[:12]),
                    quote=quote,
                )
            )
        return ExtractOut(requirements=drafts, glossary=[])
