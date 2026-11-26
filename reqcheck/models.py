"""Контракты данных агента.

Два вида моделей:
- *Draft-модели* — то, что возвращает LLM. Плоские, без dict и объединений типов,
  чтобы проходить ограничения JSON-схем провайдеров.
- *Внутренние модели* — после проверки кодом: с ID, локаторами и ссылками.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field


# ───────────────────────────── перечисления ─────────────────────────────


class Severity(StrEnum):
    CRITICAL = "CRITICAL"
    MAJOR = "MAJOR"
    MINOR = "MINOR"


SEVERITY_RANK = {Severity.CRITICAL: 0, Severity.MAJOR: 1, Severity.MINOR: 2}
SEVERITY_LABEL = {Severity.CRITICAL: "🔴 CRITICAL", Severity.MAJOR: "🟡 MAJOR", Severity.MINOR: "🟢 MINOR"}


class Criterion(StrEnum):
    # отдельное требование — ISO/IEC/IEEE 29148:2018, п. 5.2.5
    NECESSARY = "НЕОБХ"
    APPROPRIATE = "УМЕСТ"
    UNAMBIGUOUS = "ОДНОЗН"
    COMPLETE = "ПОЛН"
    SINGULAR = "ЕДИН"
    FEASIBLE = "ОСУЩ"
    VERIFIABLE = "ПРОВ"
    CORRECT = "КОРР"
    CONFORMING = "СООТВ"
    # набор требований — 29148, п. 5.2.6; IEEE 830-1998, п. 4.3
    SET_COMPLETE = "Н-ПОЛН"
    SET_CONSISTENT = "Н-СОГЛ"
    SET_FEASIBLE = "Н-ОСУЩ"
    SET_COMPREHENSIBLE = "Н-ПОНЯТ"
    SET_VALIDATABLE = "Н-ВАЛИД"
    SET_RANKED = "Н-РАНЖ"
    SET_MODIFIABLE = "Н-МОДИФ"
    SET_TRACEABLE = "Н-ТРАСС"


INDIVIDUAL_CRITERIA: list[Criterion] = [
    Criterion.NECESSARY,
    Criterion.APPROPRIATE,
    Criterion.UNAMBIGUOUS,
    Criterion.COMPLETE,
    Criterion.SINGULAR,
    Criterion.FEASIBLE,
    Criterion.VERIFIABLE,
    Criterion.CORRECT,
    Criterion.CONFORMING,
]
SET_CRITERIA: list[Criterion] = [c for c in Criterion if c.value.startswith("Н-")]

CRITERION_NAME: dict[Criterion, str] = {
    Criterion.NECESSARY: "Необходимость",
    Criterion.APPROPRIATE: "Уместность",
    Criterion.UNAMBIGUOUS: "Однозначность",
    Criterion.COMPLETE: "Полнота",
    Criterion.SINGULAR: "Единичность",
    Criterion.FEASIBLE: "Осуществимость",
    Criterion.VERIFIABLE: "Проверяемость",
    Criterion.CORRECT: "Корректность",
    Criterion.CONFORMING: "Соответствие",
    Criterion.SET_COMPLETE: "Полнота набора",
    Criterion.SET_CONSISTENT: "Непротиворечивость",
    Criterion.SET_FEASIBLE: "Осуществимость набора",
    Criterion.SET_COMPREHENSIBLE: "Понятность",
    Criterion.SET_VALIDATABLE: "Валидируемость",
    Criterion.SET_RANKED: "Ранжированность",
    Criterion.SET_MODIFIABLE: "Модифицируемость",
    Criterion.SET_TRACEABLE: "Прослеживаемость",
}

CRITERION_STANDARD: dict[Criterion, str] = {
    Criterion.NECESSARY: "29148 п. 5.2.5 a",
    Criterion.APPROPRIATE: "29148 п. 5.2.5 b",
    Criterion.UNAMBIGUOUS: "29148 п. 5.2.5 c, 5.2.7; IEEE 830 п. 4.3.2",
    Criterion.COMPLETE: "29148 п. 5.2.5 d",
    Criterion.SINGULAR: "29148 п. 5.2.5 e",
    Criterion.FEASIBLE: "29148 п. 5.2.5 f",
    Criterion.VERIFIABLE: "29148 п. 5.2.5 g; IEEE 830 п. 4.3.6",
    Criterion.CORRECT: "29148 п. 5.2.5 h; IEEE 830 п. 4.3.1",
    Criterion.CONFORMING: "29148 п. 5.2.5 i",
    Criterion.SET_COMPLETE: "29148 п. 5.2.6 a; IEEE 830 п. 4.3.3",
    Criterion.SET_CONSISTENT: "29148 п. 5.2.6 b; IEEE 830 п. 4.3.4",
    Criterion.SET_FEASIBLE: "29148 п. 5.2.6 c",
    Criterion.SET_COMPREHENSIBLE: "29148 п. 5.2.6 d",
    Criterion.SET_VALIDATABLE: "29148 п. 5.2.6 e",
    Criterion.SET_RANKED: "IEEE 830 п. 4.3.5",
    Criterion.SET_MODIFIABLE: "IEEE 830 п. 4.3.7",
    Criterion.SET_TRACEABLE: "IEEE 830 п. 4.3.8",
}


class Category(StrEnum):
    CALC = "расчёт"
    UNITS = "единицы"
    TIME = "время"
    API = "API/HTTP"
    SECURITY = "ИБ"
    DATA = "данные"
    PROCESS = "процесс"
    UI = "UI"
    NFR = "НФТ"
    TERMS = "терминология"
    TRACE = "трассировка"
    FORMAT = "оформление"
    DOMAIN = "домен"


class ReqKind(StrEnum):
    BUSINESS = "business"
    FUNCTIONAL = "functional"
    UI = "ui"
    PROCESS = "process"
    NFR = "nfr"
    DATA = "data"
    INTEGRATION = "integration"
    API = "api"
    ACCEPTANCE = "acceptance"
    ARCHITECTURE = "architecture"
    CONSTRAINT = "constraint"


KIND_LABEL: dict[ReqKind, str] = {
    ReqKind.BUSINESS: "бизнес",
    ReqKind.FUNCTIONAL: "функц.",
    ReqKind.UI: "UI",
    ReqKind.PROCESS: "процесс",
    ReqKind.NFR: "НФТ",
    ReqKind.DATA: "данные",
    ReqKind.INTEGRATION: "интеграция",
    ReqKind.API: "API",
    ReqKind.ACCEPTANCE: "приёмка",
    ReqKind.ARCHITECTURE: "архитектура",
    ReqKind.CONSTRAINT: "ограничение",
}


class Addressee(StrEnum):
    BUSINESS = "Бизнес"
    ANALYST = "Аналитик"
    ARCHITECT = "Архитектор"
    SECURITY = "ИБ"
    INTEGRATION = "Интеграции"
    FINANCE = "Финансы"
    COMPLIANCE = "Комплаенс"
    QA = "QA"


CheckStatus = Literal["ok", "warn", "fail", "na"]
GoStatus = Literal["go", "conditional", "no_go"]


# ───────────────────────────── draft-модели (ответы LLM) ─────────────────────────────


class ReqDraft(BaseModel):
    doc_id: str = Field("", description="Номер пункта в документе как есть («Ф-1», «НФТ-2», «4.2.1»). Пусто, если номера нет.")
    part: int = Field(0, description="Номер части, если пункт разбит на независимые утверждения (1, 2, …); 0 — не разбит.")
    kind: ReqKind = Field(description="Тип требования.")
    summary: str = Field("", description="Суть требования своими словами, до 20 слов.")
    quote: str = Field(description="Дословный фрагмент документа (одна-две строки), по которому требование находится поиском.")


class TermDraft(BaseModel):
    canonical: str = Field(description="Каноническое название сущности или термина.")
    variants: list[str] = Field(default_factory=list, description="Все варианты, которыми сущность названа в документе.")
    defined_in_doc: bool = Field(False, description="Есть ли в документе определение термина.")


class ExtractOut(BaseModel):
    requirements: list[ReqDraft]
    glossary: list[TermDraft] = Field(default_factory=list)


class ExtractFixOut(BaseModel):
    requirements: list[ReqDraft]


class FindingDraft(BaseModel):
    rids: list[str] = Field(default_factory=list, description="ID затронутых требований из реестра (REQ-07 и т.п.).")
    criterion: Criterion = Field(description="Код нарушенного критерия.")
    category: Category = Field(description="Тема дефекта.")
    severity: Severity = Field(description="Критичность по рубрике.")
    title: str = Field("", description="Название дефекта, до 12 слов.")
    quotes: list[str] = Field(description="1–3 дословные цитаты из документа (каждая — один фрагмент до 300 символов).")
    problem: str = Field("", description="В чём дефект, конкретно и со ссылкой на текст.")
    impact: str = Field("", description="К чему приведёт для разработки, тестирования или бизнеса.")
    scenario: str = Field("", description="Сценарий-контрпример, в котором дефект проявляется (если применимо).")
    recommendation: str = Field("", description="Конкретная правка или новая формулировка.")
    question: str = Field("", description="Уточняющий вопрос заказчику, если без ответа дефект не исправить.")


class ScenarioDraft(BaseModel):
    title: str = ""
    given: str = ""
    when: str = ""
    expected: str = Field("", description="Ожидаемый результат по документу; если не выводится — «не определено».")
    determinable: bool = Field(True, description="Можно ли однозначно вывести ожидаемый результат из документа.")
    gap: str = Field("", description="Чего не хватает в документе, если determinable=false.")


class CheckDraft(BaseModel):
    criterion: Criterion
    status: CheckStatus
    note: str = Field("", description="Пояснение; обязательно для warn и fail.")


class AssessmentDraft(BaseModel):
    rid: str
    scenarios: list[ScenarioDraft] = Field(default_factory=list)
    checks: list[CheckDraft] = Field(default_factory=list)
    findings: list[FindingDraft] = Field(default_factory=list)
    rewrite: str = Field("", description="Исправленная формулировка, если есть дефекты.")
    conclusion: str = Field("", description="Итог по требованию в 1–2 фразы, включая сильные стороны.")


class ReviewOut(BaseModel):
    assessments: list[AssessmentDraft]


class FactDraft(BaseModel):
    rid: str = Field("", description="ID требования из реестра; пусто, если факт из раздела без требований.")
    entity: str = Field(description="Сущность — каноническое название из глоссария.")
    attribute: str = Field(description="Атрибут: единица, тип, диапазон, момент, условие, значение, период…")
    value: str
    unit: str = ""
    condition: str = ""
    quote: str


class ClaimDraft(BaseModel):
    label: str = Field(description="Что считается («Выручка за год», «Срок окупаемости»).")
    rid: str = ""
    expression: str = Field(description="Арифметическое выражение в синтаксисе Python из чисел документа: 120000 * 0.9 * 1200.")
    stated_value: float = Field(description="Результат, заявленный в документе, как число (1,2 млн → 1200000).")
    unit: str = ""
    quote: str = Field(description="Дословная цитата заявленного результата или формулы.")


class FactsOut(BaseModel):
    facts: list[FactDraft] = Field(default_factory=list)
    claims: list[ClaimDraft] = Field(default_factory=list)


class PropertyDraft(BaseModel):
    criterion: Criterion
    status: Literal["ok", "warn", "fail"]
    summary: str = ""


class GroupVerdictDraft(BaseModel):
    key: str = ""
    conflict: bool = False
    note: str = ""


class ConsistencyOut(BaseModel):
    checked_groups: list[GroupVerdictDraft] = Field(default_factory=list)
    findings: list[FindingDraft] = Field(default_factory=list)
    properties: list[PropertyDraft] = Field(default_factory=list)


class TraceRowDraft(BaseModel):
    rid: str
    acceptance: list[str] = Field(default_factory=list)
    api: list[str] = Field(default_factory=list)
    data: list[str] = Field(default_factory=list)
    ui: list[str] = Field(default_factory=list)
    gaps: str = ""


class CompletenessOut(BaseModel):
    findings: list[FindingDraft] = Field(default_factory=list)
    trace: list[TraceRowDraft] = Field(default_factory=list)
    properties: list[PropertyDraft] = Field(default_factory=list)


class StandardsOut(BaseModel):
    findings: list[FindingDraft] = Field(default_factory=list)
    properties: list[PropertyDraft] = Field(default_factory=list)


class BusinessOut(BaseModel):
    extra_claims: list[ClaimDraft] = Field(default_factory=list)
    findings: list[FindingDraft] = Field(default_factory=list)


class DecisionDraft(BaseModel):
    fid: str
    action: Literal["keep", "drop", "merge", "severity"]
    target: str = Field("", description="Для merge — ID находки, в которую сливается эта.")
    new_severity: Severity = Field(Severity.MAJOR, description="Итоговая критичность (равна текущей, если не меняется).")
    reason: str = ""
    evidence_quote: str = Field("", description="Для drop — дословная цитата документа, которая опровергает находку.")


class CriticOut(BaseModel):
    decisions: list[DecisionDraft] = Field(default_factory=list)
    missed: list[FindingDraft] = Field(default_factory=list)


class FixItemDraft(BaseModel):
    fid: str
    action: str = Field(description="Что сделать, одной фразой.")


class QuestionDraft(BaseModel):
    text: str
    addressee: Addressee = Addressee.ANALYST
    why: str = Field("", description="Зачем нужен ответ: какой дефект он снимает.")
    options: list[str] = Field(default_factory=list, description="Варианты ответа.")
    recommended: str = Field("", description="Рекомендуемый вариант и почему.")
    finding_ids: list[str] = Field(default_factory=list)
    priority: int = Field(2, description="1 — блокирует разработку, 2 — важно, 3 — желательно.")


class SynthesisOut(BaseModel):
    summary: str = Field("", description="Резюме для руководителя, 3–5 фраз, со ссылками на ID дефектов.")
    rationale_dev: str = ""
    rationale_test: str = ""
    fix_first: list[FixItemDraft] = Field(default_factory=list)
    questions: list[QuestionDraft] = Field(default_factory=list)


class ClarifyDecisionDraft(BaseModel):
    fid: str
    status: Literal["resolved", "partial", "open"] = Field(
        description="resolved — ответ снимает дефект; partial — снимает часть; open — не помогает."
    )
    by: list[str] = Field(default_factory=list, description="ID вопросов (Q-01 …), ответы на которые повлияли на решение.")
    note: str = Field("", description="Что решено и что осталось, 1–2 фразы.")


class AmendmentDraft(BaseModel):
    qid: str
    wording: str = Field("", description="Готовая формулировка для документа по ответу.")
    where: str = Field("", description="Куда внести: раздел и ID требования.")


class ClarifyOut(BaseModel):
    decisions: list[ClarifyDecisionDraft] = Field(default_factory=list)
    amendments: list[AmendmentDraft] = Field(default_factory=list)
    new_findings: list[FindingDraft] = Field(default_factory=list)


# ───────────────────────────── внутренние модели ─────────────────────────────


class Locator(BaseModel):
    section_path: str
    line_start: int
    line_end: int
    page: int | None = None

    def label(self) -> str:
        lines = f"строка {self.line_start}" if self.line_start == self.line_end else f"строки {self.line_start}–{self.line_end}"
        page = f"с. {self.page}, " if self.page else ""
        return f"{self.section_path} · {page}{lines}" if self.section_path else f"{page}{lines}"


class Evidence(BaseModel):
    quote: str
    locator: Locator


class Requirement(BaseModel):
    rid: str
    doc_id: str | None = None
    doc_id_label: str = "—"
    kind: ReqKind
    section_path: str
    summary: str
    quote: str
    locator: Locator


class Term(BaseModel):
    canonical: str
    variants: list[str] = Field(default_factory=list)
    defined_in_doc: bool = False


class Fact(BaseModel):
    rid: str = ""
    entity: str
    attribute: str
    value: str
    unit: str = ""
    condition: str = ""
    evidence: Evidence


class CriterionCheck(BaseModel):
    criterion: Criterion
    status: CheckStatus
    note: str = ""


class Scenario(BaseModel):
    title: str
    given: str
    when: str
    expected: str
    determinable: bool
    gap: str = ""


class ReqAssessment(BaseModel):
    rid: str
    checks: list[CriterionCheck] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    rewrite: str = ""
    conclusion: str = ""
    reviewed: bool = True


class Finding(BaseModel):
    fid: str = ""
    rids: list[str] = Field(default_factory=list)
    scope: Literal["requirement", "set"] = "requirement"
    criterion: Criterion
    category: Category
    severity: Severity
    title: str
    problem: str
    impact: str = ""
    scenario: str = ""
    recommendation: str = ""
    evidence: list[Evidence]
    questions: list[str] = Field(default_factory=list)
    source: list[str] = Field(default_factory=list)
    confidence: float = 0.7
    hard: bool = False  # детерминированная находка правила: критик не может её удалить
    resolution: Literal["", "resolved", "partial", "open"] = ""  # учёт ответов заказчика
    resolution_note: str = ""
    resolved_by: list[str] = Field(default_factory=list)  # ID вопросов

    @property
    def card_rids(self) -> list[str]:
        """Требования, которым находка засчитывается как дефект: все, если их не больше трёх, иначе — первое.
        Находка по набору («критерии приёмки покрывают 3 сценария из 68») не делает дефектным каждое упомянутое."""
        return self.rids if len(self.rids) <= 3 else self.rids[:1]


class Signal(BaseModel):
    """Сигнал детерминированного правила."""

    rule_id: str
    criterion: Criterion
    category: Category
    severity: Severity
    hard: bool
    line: int
    quote: str
    message: str
    recommendation: str = ""
    title: str = ""


class CalcResult(BaseModel):
    cid: str
    label: str
    expression: str
    stated: float
    computed: float | None
    unit: str = ""
    status: Literal["ok", "mismatch", "error"]
    deviation_pct: float | None = None
    note: str = ""
    evidence: Evidence | None = None


class PropertyAssessment(BaseModel):
    criterion: Criterion
    status: Literal["ok", "warn", "fail", "na"]
    summary: str = ""
    finding_ids: list[str] = Field(default_factory=list)


class TraceRow(BaseModel):
    rid: str
    acceptance: list[str] = Field(default_factory=list)
    api: list[str] = Field(default_factory=list)
    data: list[str] = Field(default_factory=list)
    ui: list[str] = Field(default_factory=list)
    gaps: str = ""


class Question(BaseModel):
    qid: str
    text: str
    addressee: str
    why: str = ""
    options: list[str] = Field(default_factory=list)
    recommended: str = ""
    finding_ids: list[str] = Field(default_factory=list)
    priority: int = 2


class Answer(BaseModel):
    """Ответ заказчика на уточняющий вопрос и его учёт в ревью."""

    qid: str
    question: str
    answer: str
    options: list[str] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)  # дефекты, к которым вопрос был задан
    resolved: list[str] = Field(default_factory=list)
    partial: list[str] = Field(default_factory=list)
    still_open: list[str] = Field(default_factory=list)
    new_findings: list[str] = Field(default_factory=list)
    amendment: str = ""
    where: str = ""


class FixItem(BaseModel):
    fid: str
    action: str


class Verdict(BaseModel):
    development: GoStatus
    testing: GoStatus
    rationale_dev: str = ""
    rationale_test: str = ""
    fix_first: list[FixItem] = Field(default_factory=list)


class Stats(BaseModel):
    requirements_total: int
    requirements_with_findings: int
    requirements_clean: int
    requirements_unreviewed: int
    findings_total: int
    by_severity: dict[str, int]
    by_criterion: dict[str, int]
    by_category: dict[str, int]
    critical_unverifiable: list[str]
    critical_contradictory: list[str]
    critical_incomplete: list[str]
    critical_other: list[str]
    resolved_by_answers: int = 0
    partial_by_answers: int = 0


class RunPassport(BaseModel):
    document: str
    document_path: str
    document_format: str
    sha256: str
    started_at: datetime
    finished_at: datetime | None = None
    provider: str
    model: str
    temperature: float
    seed: int | None
    thinking: str
    agent_version: str
    llm_calls: int = 0
    cache_hits: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    run_dir: str = ""
    replay: str = ""  # трасса, из которой воспроизведён прогон
    text_integrity: str = ""
    limitations: list[str] = Field(default_factory=list)


class Report(BaseModel):
    passport: RunPassport
    summary: str
    requirements: list[Requirement]
    assessments: list[ReqAssessment]
    findings: list[Finding]
    set_properties: list[PropertyAssessment]
    trace_matrix: list[TraceRow]
    calc_checks: list[CalcResult]
    questions: list[Question]
    stats: Stats
    verdict: Verdict
    glossary: list[Term] = Field(default_factory=list)
    rejected: list[dict] = Field(default_factory=list)
    answers: list[Answer] = Field(default_factory=list)
