import pytest

from simplicio_fast.validation_cache import ValidationCache, ValidationCacheError, ValidationKey


def key() -> ValidationKey:
    return ValidationKey("sha256:source", "sha256:lock", "python-3.14", ("pytest", "tests/a.py"), (("CI", "1"),), generation="g1")


def test_validation_key_is_content_addressed_and_cache_respects_freshness() -> None:
    first = key()
    second = key()
    assert first.digest == second.digest
    cache = ValidationCache()
    stale = cache.put(first, status="pass", result={"ok": True}, fresh=False)
    assert cache.get(second) == stale
    assert cache.get(second, require_fresh=True) is None
    fresh = cache.put(first, status="pass", result={"ok": True}, fresh=True)
    assert cache.get(second, require_fresh=True) == fresh


def test_affected_selection_is_deterministic_and_bounded() -> None:
    result = ValidationCache().affected(("h2", "h1"), {"h1": ("test/z", "test/a"), "h2": ("test/b",)}, max_tests=2)
    assert result["tests"] == ["test/a", "test/b"]
    assert result["complete"] is False
    assert result["truncation_reasons"] == ["test_budget"]


def test_invalid_cache_inputs_fail_closed() -> None:
    with pytest.raises(ValidationCacheError, match="cache_key_required_missing"):
        ValidationKey("", "lock", "tool", ("test",)).to_dict()
    with pytest.raises(ValidationCacheError, match="selection_budget_invalid"):
        ValidationCache().affected(("h",), {}, max_tests=0)


def test_validation_cache_persistence_is_deterministic_and_tamper_evident(tmp_path) -> None:
    cache = ValidationCache()
    cache.put(key(), status="pass", result={"ok": True}, evidence=("receipt:1",))
    path = tmp_path / "cache.json"
    receipt = cache.save(path)
    restored = ValidationCache.load(path)
    assert receipt["entries"] == 1
    assert restored.get(key()) == cache.get(key())
    path.write_text(path.read_text(encoding="utf-8").replace("receipt:1", "receipt:2"), encoding="utf-8")
    with pytest.raises(ValidationCacheError, match="cache_digest_mismatch"):
        ValidationCache.load(path)
