"""Безопасный пересчёт арифметики из документа: LLM выписывает формулы, считает код."""

from __future__ import annotations

import ast
import math
import operator as op
import re

from reqcheck.errors import ReqcheckError


class CalcError(ReqcheckError):
    pass


def npv(rate: float, flows: list[float]) -> float:
    """Чистая приведённая стоимость: flows[0] — поток периода 0 (обычно инвестиции со знаком минус)."""
    return sum(cf / (1 + rate) ** t for t, cf in enumerate(flows))


def _pow(base: float, exp: float) -> float:
    if abs(exp) > 100 or abs(base) > 1e12:
        raise CalcError("слишком большая степень")
    return base**exp


_BINOPS = {
    ast.Add: op.add,
    ast.Sub: op.sub,
    ast.Mult: op.mul,
    ast.Div: op.truediv,
    ast.FloorDiv: op.floordiv,
    ast.Mod: op.mod,
    ast.Pow: _pow,
}
_UNARY = {ast.UAdd: op.pos, ast.USub: op.neg}
_FUNCS = {"round": round, "min": min, "max": max, "abs": abs, "sum": sum, "npv": npv}


def _prepare(expr: str) -> str:
    expr = expr.strip().replace("×", "*").replace("·", "*").replace("÷", "/").replace("−", "-").replace("^", "**")
    expr = re.sub(r"(?<=\d)[\s\u00a0\u202f\u2009](?=\d{3}(?!\d))", "", expr)  # 120 000 → 120000
    expr = re.sub(r"(\d+(?:\.\d+)?)\s*%", r"(\1/100)", expr)
    return expr


def _eval(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value  # целые остаются целыми: round(x, 2) требует int
    if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
        return _BINOPS[type(node.op)](_eval(node.left), _eval(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(e) for e in node.elts]
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in _FUNCS and not node.keywords:
        args = [_eval(a) for a in node.args]
        return _FUNCS[node.func.id](*args)
    raise CalcError(f"недопустимый элемент выражения: {type(node).__name__}")


def evaluate(expr: str) -> float:
    if not expr or len(expr) > 2000:
        raise CalcError("пустое или слишком длинное выражение")
    try:
        tree = ast.parse(_prepare(expr), mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"синтаксическая ошибка: {exc.msg}") from exc
    try:
        value = _eval(tree.body)
    except ZeroDivisionError as exc:
        raise CalcError("деление на ноль") from exc
    if isinstance(value, list) or not math.isfinite(value):
        raise CalcError("результат не является конечным числом")
    return float(value)


def tolerance(stated: float) -> float:
    """Допуск сравнения: 0,2% и точность записи заявленного числа (1 200 000 → ±50 000)."""
    rel = abs(stated) * 0.002
    if stated == 0:
        return 0.01
    digits = f"{abs(stated):.6f}".rstrip("0").rstrip(".")
    if "." in digits:
        decimals = len(digits.split(".")[1])
        rounding = 0.5 * 10 ** (-decimals)
    else:
        trailing = len(digits) - len(digits.rstrip("0"))
        rounding = 0.5 * 10 ** min(trailing, max(len(digits) - 2, 0))
    return max(rel, rounding, 0.01)


def compare(stated: float, computed: float) -> tuple[bool, float]:
    """(совпадает ли, отклонение в процентах от вычисленного)."""
    diff = abs(stated - computed)
    deviation = diff / abs(computed) * 100 if computed else (0.0 if diff == 0 else 100.0)
    return diff <= tolerance(stated), deviation


DECISION_METRIC_RE = re.compile(
    r"npv|irr|roi|ebitda|окупаем|маржа|маржинал|прибыл|выручк|доход|итог|всего|total|бюджет|безубыточ",
    re.IGNORECASE,
)
