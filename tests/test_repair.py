"""Устойчивость к повреждённому тексту: «кракозябры», невидимые символы, смешанные алфавиты, потерянные буквы."""

from reqcheck.ingest import load_document
from reqcheck.ingest.repair import RepairReport, decode_bytes, repair_line, russian_score
from reqcheck.rules import run_rules
from tests.conftest import REQUIREMENTS_MD

SRC = "Требования к данным: сумма покупки в тиынах, порог в тенге ₸ — «Ф-1»."


def _fix(line: str, pdf: bool = False) -> tuple[str, RepairReport]:
    report = RepairReport()
    return repair_line(line, 1, report, pdf=pdf), report


def test_utf8_read_as_cp1251_is_repaired():
    fixed, report = _fix(SRC.encode("utf-8").decode("cp1251"))
    assert fixed == SRC and "encoding" in report.fixed


def test_utf8_read_as_latin1_is_repaired():
    fixed, _ = _fix(SRC.encode("utf-8").decode("latin-1"))
    assert fixed == SRC


def test_cp1251_read_as_latin1_is_repaired():
    src = "Требования к данным: сумма покупки в тиынах."
    fixed, report = _fix(src.encode("cp1251").decode("latin-1"))
    assert fixed == src and "encoding" in report.fixed


def test_cp1251_read_as_cp866_is_repaired():
    src = "Месяц определяется по дате совершения покупки."
    fixed, _ = _fix(src.encode("cp1251").decode("cp866"))
    assert fixed == src


def test_partially_broken_line():
    broken = "Ф-2. " + "Месяц определяется".encode("utf-8").decode("cp1251") + " по дате покупки."
    fixed, _ = _fix(broken)
    assert fixed == "Ф-2. Месяц определяется по дате покупки."


def test_normal_text_is_untouched():
    for line in [SRC, "| `amount` | integer | 32 bit | Да | 1 000–1 000 000 |", "С 01.01.2026 — новый тариф", "Қазақстан Республикасы"]:
        fixed, report = _fix(line)
        assert fixed == line and not report.fixed and not report.broken


def test_mixed_script_words():
    fixed, report = _fix("Pасходы по карте и мeтод GЕT")  # латинская P, латинская e, кириллическая Е
    assert fixed == "Расходы по карте и метод GET"
    assert "mixed_script" in report.fixed


def test_lookalike_letters_from_other_alphabets():
    # PDF со шрифтом с неверной таблицей символов: «к» извлекается как «ĸ» (U+0138), «о» — как греческая «ο»
    fixed, report = _fix("Для чужой ĸарты возвращается 404, ĸaĸ и пο ĸ оплате; Οплата")
    assert fixed == "Для чужой карты возвращается 404, как и по к оплате; Оплата"
    assert "mixed_script" in report.fixed


def test_greek_symbols_and_latin_words_are_untouched():
    for line in ["коэффициент α равен 0,5, плотность ρ", "сервис Kafka и OK", "Wi-Fi и API"]:
        fixed, report = _fix(line)
        assert fixed == line and not report.fixed, line


def test_kazakh_letter_typed_in_latin():
    fixed, _ = _fix("Қазақ тiлiнде")  # латинская i вместо казахской і
    assert fixed == "Қазақ тілінде"


def test_decomposed_letters_are_composed():
    fixed, report = _fix("Тайм-аут и ёмкость")
    assert fixed == "Тайм-аут и ёмкость" and "unicode" in report.fixed


def test_invisible_and_pdf_soft_hyphen():
    fixed, report = _fix("Ф­3.​ Порог", pdf=True)
    assert fixed == "Ф-3. Порог" and report.invisible_removed == 1


def test_unrecoverable_corruption_is_reported_not_guessed():
    for line, kind in [
        ("Порог задаётся в ��� по курсу", "replacement"),
        ("Сумма  месяца", "private_use"),
        ("Тре?ования к ????? ?????", "question_marks"),
        ("(cid:12)(cid:15) порог", "cid"),
    ]:
        fixed, report = _fix(line)
        assert report.broken and report.broken[0][1] == kind, line


def test_russian_score_separates_text_from_garbage():
    assert russian_score("Требования к данным и интеграции") > -3.6
    assert russian_score("╥Ёхсютрэш  ъ фрээ√ь") < -4


def test_decode_bytes_detects_cp1251_and_koi8():
    text = "Требования к уведомлениям о расходах по карте"
    assert decode_bytes(text.encode("cp1251"))[0] == text
    assert decode_bytes(text.encode("koi8-r"))[0] == text
    assert decode_bytes(text.encode("utf-16"))[0] == text


def test_corrupted_document_end_to_end(tmp_path):
    lines = REQUIREMENTS_MD.read_text(encoding="utf-8").split("\n")
    lines[38] = lines[38].encode("utf-8").decode("cp1251")  # Ф-3 — «кракозябры»
    lines[45] = lines[45].replace("минуты", "������")  # НФТ — потерянные буквы
    lines[48] = lines[48].replace("Хранить", "Xpaнить")  # латиница внутри слова
    path = tmp_path / "broken.md"
    path.write_bytes("\n".join(lines).encode("utf-8"))

    doc = load_document(path)
    assert doc.lines[38].text == REQUIREMENTS_MD.read_text(encoding="utf-8").split("\n")[38]
    assert doc.lines[48].text.startswith("Хранить")
    assert doc.repair.broken and doc.repair.broken[0][0] == 46
    signals, _ = run_rules(doc)
    rules = {s.rule_id for s in signals}
    assert {"R-REPAIRED-TEXT", "R-BROKEN-TEXT"} <= rules
    assert any("восстановлен" in item for item in doc.limitations)


def test_cp1251_file_is_decoded(tmp_path):
    text = REQUIREMENTS_MD.read_text(encoding="utf-8").replace("₸", "тг").replace("→", "->")
    path = tmp_path / "cp1251.md"
    path.write_bytes(text.encode("cp1251"))
    doc = load_document(path)
    assert doc.encoding == "cp1251"
    assert "Держатель активной карты" in doc.text
