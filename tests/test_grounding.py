from reqcheck.grounding import Grounder, clean_snippet, normalize
from reqcheck.ingest.structure import build_document


def _doc(text: str):
    return build_document(
        name="t.md", path="t.md", sha256="0", fmt="md", raw_lines=[(line, None) for line in text.split("\n")]
    )


def test_normalize_maps_typography_and_markup():
    norm, idx = normalize("**Ф\u00ad3.** «Порог»\u00a0— 1\u00a0000 ₸ | `amount` <br> Ёлка")
    assert norm == 'ф-3. "порог" - 1 000 ₸ amount елка'
    assert len(norm) == len(idx)


def test_locate_exact_ignores_markdown_and_soft_hyphen():
    g = Grounder(_doc("# Требования\n\n* **Ф\u00ad3.** Когда расходы переходят порог, клиент получает push."))
    m = g.locate("Ф-3. Когда расходы переходят порог")
    assert m is not None and m.method == "exact" and m.line_start == 3


def test_locate_fragments_with_ellipsis():
    g = Grounder(_doc("| Покупка 31 января в 23:58 | Провести 1 февраля | Февральский счётчик вырос |"))
    m = g.locate("Покупка 31 января … Февральский счётчик вырос")
    assert m is not None and m.method == "fragments"


def test_locate_fuzzy_tolerates_small_differences():
    g = Grounder(_doc("Для чужой карты возвращается 404, как и для отсутствующей карты."))
    m = g.locate("Для чужой карты возвращается 404 как и для отсутствующей карты")
    assert m is not None and m.method in ("exact", "fuzzy")


def test_locate_rejects_invented_quote():
    g = Grounder(_doc("Сервис передаёт push на отправку в течение минуты после получения события."))
    assert g.locate("Система гарантирует доставку уведомления за одну секунду") is None


def test_locate_strips_line_number_prefix():
    g = Grounder(_doc("первая строка\nХранить идентификатор карты и месяц."))
    m = g.locate("0002| Хранить идентификатор карты и месяц.")
    assert m is not None and m.line_start == 2


def test_locate_prefers_occurrence_near_hint():
    g = Grounder(_doc("порог в тенге\n\n\n\n\n\nпорог в тенге"))
    assert g.locate("порог в тенге", near=(7, 7)).line_start == 7
    assert g.locate("порог в тенге", near=(1, 1)).line_start == 1


def test_clean_snippet():
    assert clean_snippet("| **Swagger** | — |") == "Swagger | —"
