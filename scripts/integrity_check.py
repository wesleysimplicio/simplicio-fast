#!/usr/bin/env python3
"""Fail on product/packaging drift between pyproject, package, README (#216)."""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    if not match:
        raise SystemExit("version missing in pyproject.toml")
    return match.group(1)


def _package_version() -> str:
    init = ROOT / "src" / "simplicio_fast" / "__init__.py"
    text = init.read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if not match:
        raise SystemExit("__version__ missing")
    return match.group(1)


def _readme_version_badge() -> str | None:
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    match = re.search(r"version-([0-9]+\.[0-9]+\.[0-9]+)", text)
    return match.group(1) if match else None


def _mapper_dep() -> str | None:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'simplicio-mapper>=([^"\s]+)', text)
    return match.group(1) if match else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit non-zero on drift")
    args = parser.parse_args(argv)
    py_v = _pyproject_version()
    pkg_v = _package_version()
    badge = _readme_version_badge()
    mapper = _mapper_dep()
    problems: list[str] = []
    if py_v != pkg_v:
        problems.append(f"version mismatch pyproject={py_v} package={pkg_v}")
    if badge and badge != py_v:
        problems.append(f"README badge version {badge} != {py_v}")
    if mapper is None:
        problems.append("simplicio-mapper dependency missing")
    # Require current mapper floor
    if mapper and mapper < "0.26.0":
        problems.append(f"simplicio-mapper floor {mapper} < 0.26.0")
    print(f"version={py_v}")
    print(f"package={pkg_v}")
    print(f"readme_badge={badge}")
    print(f"mapper_floor={mapper}")
    print(f"default_branch_policy=master")
    if problems:
        for item in problems:
            print(f"DRIFT: {item}")
        return 1 if args.check else 0
    print("OK: no integrity drift")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
