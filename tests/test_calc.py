import pytest

from reqcheck.calc import CalcError, compare, evaluate, npv, tolerance


def test_arithmetic_and_formats():
    assert evaluate("120000 * 0.9 * 1200") == pytest.approx(129_600_000)
    assert evaluate("120 000 × 1 200") == pytest.approx(144_000_000)
    assert evaluate("15% * 200") == pytest.approx(30)
    assert evaluate("2^10") == 1024
    assert evaluate("round(10/3, 2)") == pytest.approx(3.33)


def test_npv():
    assert npv(0.1, [-100, 110]) == pytest.approx(0.0)
    assert evaluate("npv(0.1, [-100, 60, 60])") == pytest.approx(-100 + 60 / 1.1 + 60 / 1.21)


@pytest.mark.parametrize("expr", ["__import__('os').system('x')", "a + 1", "(1).real", "open('f')", "[1, 2]", "1/0", "10**10**10"])
def test_rejects_unsafe_or_invalid(expr):
    with pytest.raises(CalcError):
        evaluate(expr)


def test_compare_respects_rounding_of_stated_value():
    assert compare(1_200_000, 1_234_000)[0]  # «1,2 млн» — допуск ±50 000
    assert not compare(144_000_000, 129_600_000)[0]
    assert compare(0.15, 0.1500001)[0]
    assert tolerance(144_000_000) == 500_000
