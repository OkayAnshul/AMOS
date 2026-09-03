"""Arithmetic evaluation.

**This does not use `eval()`.** `eval` on model-generated text is arbitrary code
execution driven by an untrusted source — the model can be steered into emitting
whatever a prompt-injection payload asks for.

Instead the expression is parsed to an AST and walked with an explicit allowlist
of node types. Anything not on the list — a name, a call, an attribute access, a
subscript — is rejected before evaluation. That is the difference between "we
told the model not to" and "the code cannot".
"""

from __future__ import annotations

import ast
import operator
from collections.abc import Callable
from typing import Any, ClassVar

from pydantic import BaseModel, Field

from amos.errors import ToolValidationError
from amos.tools.base import Permission, Tool

Number = float | int

# Only these operations exist. Everything else is a rejection.
_BINARY_OPS: dict[type[ast.operator], Callable[[Number, Number], Number]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS: dict[type[ast.unaryop], Callable[[Number], Number]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}

# 2**64 is already absurd for arithmetic; 9**9**9 would hang the process.
_MAX_EXPONENT = 64
_MAX_EXPRESSION_LENGTH = 200


class CalculatorArgs(BaseModel):
    expression: str = Field(
        max_length=_MAX_EXPRESSION_LENGTH,
        description=(
            "Arithmetic expression using numbers and + - * / // % ** and parentheses. "
            "No variables, no function calls. Example: '2340 * 0.17'"
        ),
    )


class CalculatorTool(Tool):
    name: ClassVar[str] = "calculator"
    description: ClassVar[str] = (
        "Evaluate an arithmetic expression exactly. Use this instead of doing arithmetic "
        "yourself — you are unreliable at it and this is not."
    )
    input_schema: ClassVar[type[BaseModel]] = CalculatorArgs
    permission: ClassVar[Permission] = Permission.PURE
    timeout_seconds: ClassVar[float] = 2.0

    async def _run(self, args: CalculatorArgs) -> dict[str, Any]:
        try:
            tree = ast.parse(args.expression, mode="eval")
        except SyntaxError as exc:
            raise ToolValidationError(f"Not a valid expression: {exc.msg}") from exc

        result = _evaluate(tree.body)
        return {"expression": args.expression, "result": result}


def _evaluate(node: ast.expr) -> float | int:
    """Walk the AST, permitting only arithmetic."""
    match node:
        case ast.Constant(value=bool()):
            # bool is a subclass of int; excluded so `True + 1` is not arithmetic
            raise ToolValidationError("Booleans are not valid in an arithmetic expression")

        case ast.Constant(value=int() | float() as value):
            return value

        case ast.BinOp(left=left, op=op, right=right):
            func = _BINARY_OPS.get(type(op))
            if func is None:
                raise ToolValidationError(f"Operator {type(op).__name__} is not permitted")
            lhs, rhs = _evaluate(left), _evaluate(right)
            if isinstance(op, ast.Pow) and abs(rhs) > _MAX_EXPONENT:
                raise ToolValidationError(f"Exponent too large (limit {_MAX_EXPONENT})")
            try:
                return func(lhs, rhs)
            except ZeroDivisionError as exc:
                raise ToolValidationError("Division by zero") from exc

        case ast.UnaryOp(op=op, operand=operand):
            unary = _UNARY_OPS.get(type(op))
            if unary is None:
                raise ToolValidationError(f"Unary {type(op).__name__} is not permitted")
            return unary(_evaluate(operand))

        case _:
            raise ToolValidationError(
                f"{type(node).__name__} is not permitted in an arithmetic expression"
            )
