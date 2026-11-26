"""AST-based safe mathematical expression evaluator (no eval/exec)."""

from __future__ import annotations

import ast
import math
import operator
from collections.abc import Callable
from typing import Any

MAX_EXPR_LENGTH = 256
MAX_AST_NODES = 64
MAX_EXPONENT = 100
MAX_ABS_VALUE = 1e15

_BINARY_OPS: dict[type[ast.operator], Callable[[float, float], float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}

_UNARY_OPS: dict[type[ast.unaryop], Callable[[float], float]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

_SAFE_FUNCS: dict[str, Callable[..., float]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "ceil": math.ceil,
    "floor": math.floor,
    "log": math.log,
    "log10": math.log10,
}

_SAFE_CONSTANTS: dict[str, float] = {
    "pi": math.pi,
    "e": math.e,
}


class MathEvaluationError(ValueError):
    """Raised when an expression is unsafe or invalid."""


def _check_bounds(value: float) -> float:
    if isinstance(value, complex):
        raise MathEvaluationError("Complex numbers are not supported.")
    if math.isnan(value) or math.isinf(value):
        raise MathEvaluationError("Result is not a finite number.")
    if abs(value) > MAX_ABS_VALUE:
        raise MathEvaluationError(f"Result exceeds magnitude limit ({MAX_ABS_VALUE:g}).")
    return value


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)

    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise MathEvaluationError(f"Unsupported constant type: {type(node.value).__name__}")
        return _check_bounds(float(node.value))

    if isinstance(node, ast.Name):
        if node.id in _SAFE_CONSTANTS:
            return _SAFE_CONSTANTS[node.id]
        raise MathEvaluationError(f"Unknown identifier: {node.id!r}")

    if isinstance(node, ast.UnaryOp):
        unary_fn = _UNARY_OPS.get(type(node.op))
        if unary_fn is None:
            raise MathEvaluationError(f"Unsupported unary operator: {type(node.op).__name__}")
        return _check_bounds(unary_fn(_eval_node(node.operand)))

    if isinstance(node, ast.BinOp):
        bin_fn = _BINARY_OPS.get(type(node.op))
        if bin_fn is None:
            raise MathEvaluationError(f"Unsupported operator: {type(node.op).__name__}")
        left = _eval_node(node.left)
        right = _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise MathEvaluationError(f"Exponent {right} exceeds limit {MAX_EXPONENT}.")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise MathEvaluationError("Division by zero.")
        try:
            result = bin_fn(left, right)
        except (OverflowError, ZeroDivisionError, ValueError) as exc:
            raise MathEvaluationError(str(exc)) from exc
        return _check_bounds(float(result))

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name):
            raise MathEvaluationError("Only direct calls to whitelisted math functions are allowed.")
        if node.keywords:
            raise MathEvaluationError("Keyword arguments are not supported.")
        fn = _SAFE_FUNCS.get(node.func.id)
        if fn is None:
            raise MathEvaluationError(f"Function not allowed: {node.func.id!r}")
        args: list[Any] = [_eval_node(arg) for arg in node.args]
        if not args or len(args) > 8:
            raise MathEvaluationError("Function requires between 1 and 8 arguments.")
        if node.func.id == "round" and len(args) == 2:
            args[1] = int(args[1])
        try:
            res = fn(*args)
        except (ValueError, TypeError, OverflowError) as exc:
            raise MathEvaluationError(str(exc)) from exc
        return _check_bounds(float(res))

    raise MathEvaluationError(f"Disallowed syntax element: {type(node).__name__}")


def safe_eval_math(expression: str) -> float:
    """Safely evaluate a bounded arithmetic expression using Python's AST."""
    cleaned = expression.strip()
    if not cleaned:
        raise MathEvaluationError("Expression is empty.")
    if len(cleaned) > MAX_EXPR_LENGTH:
        raise MathEvaluationError(f"Expression exceeds maximum length ({MAX_EXPR_LENGTH}).")

    try:
        tree = ast.parse(cleaned, mode="eval")
    except SyntaxError as exc:
        raise MathEvaluationError(f"Invalid syntax: {exc.msg}") from exc

    node_count = sum(1 for _ in ast.walk(tree))
    if node_count > MAX_AST_NODES:
        raise MathEvaluationError(f"Expression is too complex ({node_count} > {MAX_AST_NODES} AST nodes).")

    value = _eval_node(tree)
    return float(value)
