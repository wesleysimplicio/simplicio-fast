import copy
import json
from pathlib import Path
import shutil

from scripts.check_release_integrity import SCHEMA, evaluate, main


ROOT = Path(__file__).resolve().parents[1]


def _fixture(tmp_path: Path) -> Path:
    for relative in (
        "release-policy.json",
        "pyproject.toml",
        "README.md",
        "src/simplicio_fast/__init__.py",
        ".github/workflows/native-release.yml",
        "docs/native-backend-support.md",
    ):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, target)
    return tmp_path


def test_repository_release_metadata_is_consistent():
    receipt = evaluate(ROOT)
    assert receipt["schema"] == SCHEMA
    assert receipt["status"] == "pass", receipt
    assert receipt["version"] == "2.0.14"
    assert len(receipt["runtime_dependencies"]) == 2


def test_version_drift_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    init = root / "src/simplicio_fast/__init__.py"
    init.write_text(init.read_text().replace('"2.0.14"', '"9.9.9"'), encoding="utf-8")
    receipt = evaluate(root)
    assert receipt["status"] == "fail"
    assert "package_version" in receipt["failures"]


def test_dependency_badge_drift_fails_closed(tmp_path):
    root = _fixture(tmp_path)
    readme = root / "README.md"
    readme.write_text(
        readme.read_text().replace("runtime_dependencies-2-", "runtime_dependencies-0-"),
        encoding="utf-8",
    )
    receipt = evaluate(root)
    assert "readme_dependencies" in receipt["failures"]


def test_native_ownership_and_platform_drift_fail_closed(tmp_path):
    root = _fixture(tmp_path)
    policy_path = root / "release-policy.json"
    policy = json.loads(policy_path.read_text())
    policy["native_execution_owner"] = "simplicio-fast"
    policy["supported_native_platforms"].append("plan9-mips")
    policy_path.write_text(json.dumps(policy), encoding="utf-8")
    receipt = evaluate(root)
    assert {"native_ownership", "native_platform_matrix"} <= set(receipt["failures"])


def test_check_mode_returns_nonzero_for_drift(tmp_path):
    root = _fixture(tmp_path)
    (root / "README.md").write_text("stale", encoding="utf-8")
    assert main(["--root", str(root), "--check", "--json"]) == 1
