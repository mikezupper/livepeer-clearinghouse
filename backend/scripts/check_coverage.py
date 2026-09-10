"""Enforce backend line, statement, branch, and function coverage independently."""

from __future__ import annotations

import ast
import json
from pathlib import Path

THRESHOLD = 85.0
ROOT = Path(__file__).resolve().parents[2]
COVERAGE_FILE = ROOT / "backend" / "coverage.json"
SOURCE_ROOT = ROOT / "backend" / "src" / "clearinghouse"


def percentage(covered: int, total: int) -> float:
    """Calculate a stable percentage, treating an empty category as complete."""
    return 100.0 if total == 0 else covered * 100.0 / total


def function_lines(path: Path) -> list[set[int]]:
    """Return executable source lines belonging to every declared function."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    functions: list[set[int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if all(
                isinstance(statement, ast.Expr)
                and isinstance(statement.value, ast.Constant)
                and statement.value.value is Ellipsis
                for statement in node.body
            ):
                continue
            lines = {
                child.lineno
                for child in ast.walk(node)
                if isinstance(child, ast.stmt) and child is not node
            }
            functions.append(lines)
    return functions


def coverage_metrics(report: dict[str, object]) -> dict[str, float]:
    """Derive independently enforceable coverage metrics from coverage.py JSON."""
    totals = report["totals"]
    files = report["files"]
    if not isinstance(totals, dict) or not isinstance(files, dict):
        raise TypeError("invalid coverage report")
    line_rate = percentage(int(totals["covered_lines"]), int(totals["num_statements"]))
    branch_rate = percentage(int(totals["covered_branches"]), int(totals["num_branches"]))
    covered_functions = 0
    all_functions = 0
    for source_path in SOURCE_ROOT.rglob("*.py"):
        relative = source_path.relative_to(ROOT).as_posix()
        file_report = files.get(relative, {})
        executed = (
            set(file_report.get("executed_lines", [])) if isinstance(file_report, dict) else set()
        )
        for lines in function_lines(source_path):
            all_functions += 1
            covered_functions += bool(lines & executed)
    return {
        "lines": line_rate,
        "statements": line_rate,
        "branches": branch_rate,
        "functions": percentage(covered_functions, all_functions),
    }


def main() -> int:
    """Print each metric and fail when any one is below the policy threshold."""
    report = json.loads(COVERAGE_FILE.read_text(encoding="utf-8"))
    metrics = coverage_metrics(report)
    for name, value in metrics.items():
        print(f"backend {name}: {value:.2f}%")
    return int(any(value < THRESHOLD for value in metrics.values()))


if __name__ == "__main__":
    raise SystemExit(main())
