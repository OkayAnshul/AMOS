"""The calculator's real job is refusing things.

Arithmetic correctness is the easy half. The half that matters is that
model-supplied text cannot reach `eval`.
"""

from __future__ import annotations

import pytest

from amos.tools.base import ToolCall, ToolStatus
from amos.tools.builtin import CalculatorTool


def call(expression: str) -> ToolCall:
    return ToolCall(id="c1", name="calculator", arguments={"expression": expression})


@pytest.fixture
def calculator() -> CalculatorTool:
    return CalculatorTool()


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("2340 * 0.17", 397.8),
        ("2 + 3 * 4", 14),
        ("(2 + 3) * 4", 20),
        ("10 / 4", 2.5),
        ("10 // 3", 3),
        ("10 % 3", 1),
        ("2 ** 10", 1024),
        ("-5 + 3", -2),
        ("+7", 7),
    ],
)
async def test_arithmetic_is_correct(
    calculator: CalculatorTool, expression: str, expected: float
) -> None:
    outcome = await calculator.execute(call(expression))
    assert outcome.status is ToolStatus.OK
    assert outcome.output is not None
    assert outcome.output["result"] == pytest.approx(expected)


@pytest.mark.parametrize(
    "malicious",
    [
        "__import__('os').system('rm -rf /')",
        "open('/etc/passwd').read()",
        "eval('1+1')",
        "().__class__.__bases__[0]",
        "globals()",
        "x + 1",  # bare name
        "[1,2,3][0]",  # subscript
        "lambda: 1",
        "print('hi')",
    ],
)
async def test_code_execution_is_rejected(calculator: CalculatorTool, malicious: str) -> None:
    """Not 'the prompt discourages it' — the AST walker cannot evaluate it."""
    outcome = await calculator.execute(call(malicious))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert outcome.output is None


async def test_huge_exponent_is_rejected(calculator: CalculatorTool) -> None:
    """9**9**9 would hang the process. A timeout would not save us — it blocks
    the event loop, so the guard has to be before evaluation."""
    outcome = await calculator.execute(call("9**9**9"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "Exponent too large" in (outcome.error or "")


async def test_division_by_zero_is_a_clean_error(calculator: CalculatorTool) -> None:
    outcome = await calculator.execute(call("1/0"))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "zero" in (outcome.error or "").lower()


async def test_booleans_are_not_arithmetic(calculator: CalculatorTool) -> None:
    """bool subclasses int, so `True + 1` would silently evaluate to 2."""
    outcome = await calculator.execute(call("True + 1"))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_syntax_error_is_reported_not_raised(calculator: CalculatorTool) -> None:
    outcome = await calculator.execute(call("2 +"))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_overlong_expression_fails_schema_validation(
    calculator: CalculatorTool,
) -> None:
    outcome = await calculator.execute(call("1+" * 200 + "1"))
    assert outcome.status is ToolStatus.INVALID_ARGS


async def test_missing_argument_is_invalid_args(calculator: CalculatorTool) -> None:
    outcome = await calculator.execute(ToolCall(id="c", name="calculator", arguments={}))
    assert outcome.status is ToolStatus.INVALID_ARGS
    assert "expression" in (outcome.error or "")


async def test_outcome_records_the_arguments(calculator: CalculatorTool) -> None:
    """Carried through on success and failure alike, so the trace is complete."""
    ok = await calculator.execute(call("2+2"))
    assert ok.arguments == {"expression": "2+2"}

    bad = await calculator.execute(call("open('/etc/passwd')"))
    assert bad.arguments == {"expression": "open('/etc/passwd')"}
