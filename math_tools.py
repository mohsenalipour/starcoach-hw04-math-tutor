"""Bounded symbolic checks. User/model expressions are parsed as an AST, never eval'd."""

from __future__ import annotations

import ast
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Literal

import sympy as sp
from pydantic import BaseModel, ConfigDict, Field


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["differentiate", "integrate", "solve", "simplify", "determinant"]
    expression: str = Field(min_length=1, max_length=400)
    variable: Literal["x", "y", "z", "t", "n"] = "x"
    lower_bound: str | None = Field(default=None, max_length=40)
    upper_bound: str | None = Field(default=None, max_length=40)


SYMBOLS = {name: sp.Symbol(name, real=True) for name in ("x", "y", "z", "t", "n")}
CONSTANTS = {"pi": sp.pi, "e": sp.E, "E": sp.E}
FUNCTIONS = {
    "sin": sp.sin, "cos": sp.cos, "tan": sp.tan, "exp": sp.exp,
    "log": sp.log, "ln": sp.log, "sqrt": sp.sqrt, "abs": sp.Abs,
}
DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def parse_expression(text: str) -> sp.Expr:
    text = text.translate(DIGITS).replace("^", "**").replace("−", "-")
    text = text.replace("×", "*").replace("÷", "/").strip()
    if not text or len(text) > 400:
        raise ValueError("Expression must contain 1 to 400 characters.")
    tree = ast.parse(text, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Expression is too complex for a local check.")

    def visit(node: ast.AST, depth: int = 0) -> sp.Expr:
        if depth > 15:
            raise ValueError("Expression is too deeply nested.")
        if isinstance(node, ast.Constant) and type(node.value) in (int, float):
            if not -1_000_000 <= node.value <= 1_000_000:
                raise ValueError("Number is outside the supported range.")
            return sp.Rational(str(node.value))
        if isinstance(node, ast.Name):
            if node.id in SYMBOLS:
                return SYMBOLS[node.id]
            if node.id in CONSTANTS:
                return CONSTANTS[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value = visit(node.operand, depth + 1)
            return -value if isinstance(node.op, ast.USub) else value
        if isinstance(node, ast.BinOp):
            left, right = visit(node.left, depth + 1), visit(node.right, depth + 1)
            if isinstance(node.op, ast.Add):
                return left + right
            if isinstance(node.op, ast.Sub):
                return left - right
            if isinstance(node.op, ast.Mult):
                return left * right
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise ValueError("Division by zero.")
                return left / right
            if isinstance(node.op, ast.Pow):
                if not right.is_number or right.is_real is not True or abs(right) > 12:
                    raise ValueError("Only numeric exponents between -12 and 12 are supported.")
                return left ** right
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in FUNCTIONS and len(node.args) == 1 and not node.keywords):
            return FUNCTIONS[node.func.id](visit(node.args[0], depth + 1))
        raise ValueError("Use explicit arithmetic and supported functions (e.g. 2*x + sin(x)).")

    result = visit(tree.body)
    if result.has(sp.zoo, sp.nan, sp.oo, -sp.oo):
        raise ValueError("Expression has an undefined or infinite value.")
    return result


def calculate(request: ToolRequest, candidate: str = "") -> dict:
    """Run only inside the timeout-controlled worker (also used by unit tests)."""
    variable = SYMBOLS[request.variable]
    operation = request.operation
    if operation == "determinant":
        matrix = ast.literal_eval(request.expression.translate(DIGITS))
        if (not isinstance(matrix, list) or not 1 <= len(matrix) <= 4
                or any(not isinstance(row, list) or len(row) != len(matrix) for row in matrix)):
            raise ValueError("Only square numeric matrices up to 4 by 4 are supported.")
        if any(type(v) not in (int, float) or abs(v) > 1000000 for row in matrix for v in row):
            raise ValueError("Matrix entries must be finite, bounded numbers.")
        result = sp.Matrix([[sp.Rational(str(v)) for v in row] for row in matrix]).det()
        expression = None
    else:
        text = request.expression
        if operation == "solve" and "=" in text:
            if text.count("=") != 1:
                raise ValueError("Only one equation is supported.")
            left, right = text.split("=")
            expression = parse_expression(left) - parse_expression(right)
        else:
            expression = parse_expression(text)
        if operation == "differentiate":
            result = sp.diff(expression, variable)
        elif operation == "integrate":
            has_bounds = request.lower_bound is not None or request.upper_bound is not None
            if has_bounds:
                if request.lower_bound is None or request.upper_bound is None:
                    raise ValueError("A definite integral requires both bounds.")
                lower, upper = parse_expression(request.lower_bound), parse_expression(request.upper_bound)
                if lower.free_symbols or upper.free_symbols:
                    raise ValueError("Integral bounds must be constants.")
                result = sp.integrate(expression, (variable, lower, upper))
            else:
                result = sp.integrate(expression, variable)
            if result.has(sp.Integral):
                raise ValueError("SymPy could not evaluate this integral.")
        elif operation == "solve":
            if expression.free_symbols - {variable} or not expression.is_polynomial(variable):
                raise ValueError("Local equation checks support one-variable polynomial equations.")
            if sp.degree(expression, variable) > 6:
                raise ValueError("Polynomial degree must be at most six.")
            if expression == 0:
                raise ValueError("The equation is an identity; every real value is a solution.")
            result = sp.solveset(expression, variable, domain=sp.S.Reals)
        else:
            result = sp.simplify(expression)
    if result.has(sp.nan, sp.zoo, sp.oo, -sp.oo):
        raise ValueError("The symbolic result is undefined or infinite.")

    matches = None
    if candidate.strip():
        try:
            if operation == "solve":
                values = candidate.strip().strip("{}[]").split(",")
                parsed = sp.FiniteSet(*(parse_expression(v) for v in values if v.strip()))
                matches = bool(parsed == result)
            elif operation == "integrate" and request.lower_bound is None:
                # An arbitrary additive constant is allowed; differentiate to verify.
                clean = re.sub(r"\s*[+]\s*C\s*$", "", candidate.strip())
                matches = bool(sp.simplify(sp.diff(parse_expression(clean), variable) - expression) == 0)
            else:
                matches = bool(sp.simplify(parse_expression(candidate) - result) == 0)
        except (ValueError, SyntaxError, TypeError):
            pass
    return {
        "status": "computed", "operation": operation,
        "expression": request.expression, "variable": request.variable,
        "lower_bound": request.lower_bound, "upper_bound": request.upper_bound,
        "result": str(result), "candidate_matches": matches,
        "note": "Indefinite integrals require + C." if operation == "integrate" and request.lower_bound is None else "",
    }


def run_calculation(request: ToolRequest | None, candidate: str = "", timeout: float = 6) -> dict:
    if request is None:
        return {"status": "unavailable", "reason": "No supported symbolic operation identified."}
    payload = {"request": request.model_dump(), "candidate": candidate[:400]}
    try:
        process = subprocess.run(
            [sys.executable, str(Path(__file__).resolve()), "--worker"],
            input=json.dumps(payload, ensure_ascii=False), encoding="utf-8",
            capture_output=True, timeout=timeout, check=False,
        )
    except subprocess.TimeoutExpired:
        return {"status": "unavailable", "reason": "The local calculation exceeded its time limit."}
    except OSError:
        return {"status": "unavailable", "reason": "The symbolic worker could not start."}
    if process.returncode != 0:
        return {"status": "unavailable", "reason": "The symbolic worker failed."}
    try:
        return json.loads(process.stdout)
    except json.JSONDecodeError:
        return {"status": "unavailable", "reason": "The symbolic worker returned invalid data."}


if __name__ == "__main__" and sys.argv[1:] == ["--worker"]:
    try:
        data = json.loads(sys.stdin.read(16000))
        output = calculate(ToolRequest.model_validate(data["request"]), data.get("candidate", ""))
    except (ValueError, SyntaxError, TypeError, KeyError, NotImplementedError, OverflowError, ZeroDivisionError):
        output = {"status": "unavailable", "reason": "Expression or operation is outside the local tool's supported scope."}
    print(json.dumps(output, ensure_ascii=True))
