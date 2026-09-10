"""Enforce inward-only backend package dependencies."""

from __future__ import annotations

import ast
from pathlib import Path

SOURCE = Path(__file__).resolve().parents[2] / "src" / "clearinghouse"
FORBIDDEN = {
    "domain": (
        "fastapi",
        "sqlalchemy",
        "pydantic",
        "structlog",
        "clearinghouse.application",
        "clearinghouse.adapters",
        "clearinghouse.infrastructure",
    ),
    "application": (
        "fastapi",
        "sqlalchemy",
        "pydantic",
        "structlog",
        "clearinghouse.adapters",
        "clearinghouse.infrastructure",
    ),
}


def imported_modules(path: Path) -> set[str]:
    """Collect absolute import targets from a Python source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_dependencies_point_inward() -> None:
    violations: list[str] = []
    for layer, forbidden_prefixes in FORBIDDEN.items():
        for path in (SOURCE / layer).rglob("*.py"):
            for module in imported_modules(path):
                if module.startswith(forbidden_prefixes):
                    violations.append(f"{path.relative_to(SOURCE)} imports {module}")
    assert not violations, "Forbidden backend dependencies:\n" + "\n".join(violations)
