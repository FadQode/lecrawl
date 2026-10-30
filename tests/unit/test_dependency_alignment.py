"""### Guard test keeping `pyproject.toml` and the requirements files aligned."""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DISTRIBUTION_NAME = re.compile(r"^[A-Za-z0-9._-]+")


def declared_names(specifications: list[str]) -> set[str]:
    """### Reduce requirement specifications to normalized distribution names."""
    names: set[str] = set()
    for specification in specifications:
        match = DISTRIBUTION_NAME.match(specification.strip())
        if match:
            names.add(match.group(0).lower())
    return names


def requirement_names(path: Path) -> set[str]:
    """### Collect distribution names from a requirements file, ignoring includes."""
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return declared_names([line for line in lines if not line.lstrip().startswith(("-", "#"))])


def test_runtime_dependencies_match_requirements_txt() -> None:
    """### The project metadata and requirements.txt declare the same runtime set."""
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared_names(metadata["project"]["dependencies"]) == requirement_names(PROJECT_ROOT / "requirements.txt")


def test_dev_dependencies_match_requirements_dev_txt() -> None:
    """### The development dependency group and requirements-dev.txt agree."""
    metadata = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert declared_names(metadata["dependency-groups"]["dev"]) == requirement_names(
        PROJECT_ROOT / "requirements-dev.txt"
    )
