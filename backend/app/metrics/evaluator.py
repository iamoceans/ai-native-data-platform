"""Decimal evaluation of the compiler's closed metric formula grammar."""
from __future__ import annotations

import ast
from decimal import Decimal, ROUND_HALF_UP

from app.analysis.types import AnalysisError
from app.metrics.compiler import compile_formula


def evaluate_formula(formula: str, components: dict[str, Decimal | None]) -> Decimal | None:
    # Validate with exactly the same identifier/function grammar as SQL assembly.
    compile_formula(formula, {name: name for name in components})
    tree = ast.parse(formula, mode="eval")

    def value(node: ast.AST) -> Decimal | None:
        if isinstance(node, ast.Constant):
            return Decimal(ast.get_source_segment(formula, node))
        if isinstance(node, ast.Name):
            return components[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            operand = value(node.operand)
            return -operand if operand is not None else None
        if isinstance(node, ast.BinOp):
            left, right = value(node.left), value(node.right)
            if left is None or right is None:
                return None
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise AnalysisError("METRIC_VALUE_UNDEFINED", "metric division by zero")
                return left / right
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            name = node.func.id.lower()
            if name == "coalesce":
                for arg in node.args:
                    result = value(arg)
                    if result is not None:
                        return result
                return None
            args = [value(arg) for arg in node.args]
            if name == "nullif":
                return None if args[0] == args[1] else args[0]
            if args[0] is None:
                return None
            if name == "abs":
                return abs(args[0])
            if name == "round":
                places = args[1] if len(args) == 2 else Decimal(0)
                if places is None:
                    return None
                if places != places.to_integral_value() or abs(places) > 100:
                    raise AnalysisError("METRIC_VALUE_UNDEFINED", "invalid round precision")
                return args[0].quantize(Decimal(1).scaleb(-int(places)), rounding=ROUND_HALF_UP)
        raise AnalysisError("METRIC_FORMULA_INVALID", "unsupported formula node")

    return value(tree.body)


def additive_composition(formula: str) -> bool:
    """Conservatively recognize source sums/differences for contribution charts.

    General ratios and nonlinear functions are valid period formulas, but their
    per-segment deltas are not additive contributions to the overall delta.
    """
    def additive(node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return True
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
            return additive(node.operand)
        return (
            isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub))
            and additive(node.left) and additive(node.right)
        )
    return additive(ast.parse(formula, mode="eval").body)
