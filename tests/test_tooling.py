"""The dependency pins in `pyproject.toml`, checked against what CI installs.

CI pins its tool versions inline (`pip install pytest==9.1.1`), and
`pyproject.toml` pins the same tools in dependency groups. Two sources of truth
for one set of versions drift the moment someone bumps one of them, and the
symptom is the worst kind: CI stays green while a contributor's local run uses
a different pytest.

So this reads both and requires they agree. Bump either one and this fails
until the other matches.

It parses the workflow with a small regex rather than a YAML library, because
adding a test-only dependency to check a dependency file would be its own
small joke.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CI = (REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
PYPROJECT = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
GROUPS = PYPROJECT["dependency-groups"]

#: `- run: python -m pip install pytest==9.1.1`
PIP_INSTALL = re.compile(r"pip install ([^\n]+)")


def _ci_pins() -> dict[str, str]:
    pins = {}
    for line in PIP_INSTALL.findall(CI):
        for token in line.split():
            if "==" in token:
                name, version = token.split("==", 1)
                pins[name.lower()] = version
    return pins


def _group_pins(group: str) -> dict[str, str]:
    pins = {}
    for spec in GROUPS[group]:
        if "==" in spec:
            name, version = spec.split("==", 1)
            pins[name.lower()] = version
    return pins


def test_ci_installs_something_we_recognise():
    """Guard the parser: if CI stops using `pip install`, this test is blind."""
    assert _ci_pins(), "no pinned `pip install` found in ci.yml -- update this parser"


def test_test_and_build_groups_match_ci_exactly():
    declared = {**_group_pins("test"), **_group_pins("build")}
    assert declared == _ci_pins(), (
        "pyproject.toml and .github/workflows/ci.yml disagree.\n"
        f"  pyproject: {declared}\n"
        f"  ci.yml:    {_ci_pins()}"
    )


def test_the_addon_declares_no_runtime_dependencies():
    """The addon runs on NVDA's Python. Anything here would not ship with it."""
    assert PYPROJECT["project"]["dependencies"] == []
    assert PYPROJECT["tool"]["uv"]["package"] is False


def test_python_floor_matches_what_ci_runs():
    ci_python = re.search(r'python-version:\s*"([\d.]+)"', CI).group(1)
    requires = PYPROJECT["project"]["requires-python"]
    assert requires == f">={ci_python}", (
        f"pyproject requires {requires} but CI runs {ci_python}"
    )
    pinned = (REPO / ".python-version").read_text(encoding="utf-8").strip()
    assert pinned == ci_python, (
        f".python-version says {pinned}, CI runs {ci_python}"
    )
