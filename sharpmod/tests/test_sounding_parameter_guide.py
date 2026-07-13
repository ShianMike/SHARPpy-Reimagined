"""Integrity checks for the code-aligned sounding parameter guide."""

from __future__ import annotations

import re
from pathlib import Path


GUIDE = Path(__file__).resolve().parents[2] / "docs" / "sounding_parameter_guide.md"


def _text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _math_blocks(text: str) -> list[str]:
    return re.findall(r"^```math\s*$\n(.*?)^```\s*$", text, re.MULTILINE | re.DOTALL)


def _balanced_braces(expression: str) -> bool:
    depth = 0
    escaped = False
    for char in expression:
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def test_guide_uses_github_math_syntax_consistently() -> None:
    text = _text()

    assert "$$" not in text
    assert text.count("```math") >= 40
    assert text.count("```math") == len(_math_blocks(text))

    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if line.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        without_code = re.sub(r"`[^`]*`", "", line)
        dollars = re.findall(r"(?<!\\)\$", without_code)
        assert len(dollars) % 2 == 0, f"unpaired inline math on line {line_number}"


def test_all_math_expressions_have_balanced_braces() -> None:
    text = _text()
    expressions = _math_blocks(text)

    outside_fences = re.sub(
        r"^```.*?^```\s*$", "", text, flags=re.MULTILINE | re.DOTALL
    )
    expressions.extend(re.findall(r"(?<!\\)\$(.+?)(?<!\\)\$", outside_fences))

    assert expressions
    for expression in expressions:
        assert _balanced_braces(expression), f"unbalanced braces in: {expression!r}"


def test_critical_implementation_corrections_are_documented() -> None:
    text = _text()

    required = (
        "The GUI and CLI expose six parcel keys",
        "The default integration is from the surface to 400 hPa",
        "surface minus 150 hPa to surface minus 350 hPa",
        "does **not** apply a virtual-temperature correction",
        "two similarly labeled values that must not be conflated",
        "both kinematic factors are in knots",
        "is the linear predictor (logit), not a probability",
        "It is **not** the displayed NCAPE value above",
        "the top is the **last passing level**",
        "LCL | Neutral white",
    )
    for statement in required:
        assert statement in text


def test_fixed_stp_and_ecape_equations_are_not_regressed() -> None:
    text = _text()

    assert r"\mathrm{STP}_{fix}=\frac{\mathrm{SBCAPE}}{1500}" in text
    assert r"\frac{\mathrm{SRH}_{0-1\,km}}{150}" in text
    assert r"a=\frac{\psi}{V_{SR}^{2}}" in text
    assert r"\mathrm{CAPE}-\psi N" in text
    assert r"k^2=0.18" in text
