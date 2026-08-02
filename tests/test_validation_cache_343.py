from concurrent.futures import ThreadPoolExecutor

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


def test_validation_cache_requires_provenance_for_reusable_verified_hits() -> None:
    cache = ValidationCache()
    item = cache.put(key(), status="pass", result={"ok": True}, fresh=True)
    assert item.verified is False
    assert cache.get(key(), reusable=True) is None
    verified = cache.put(
        key(), status="pass", result={"ok": True}, fresh=True,
        verified=True, provenance=("runtime:receipt:1",), evidence=("receipt:1",),
    )
    assert cache.get(key(), reusable=True) == verified


def test_validation_cache_demotes_conflicting_result_and_explains_affected_tests() -> None:
    cache = ValidationCache()
    cache.put(key(), status="pass", result={"ok": True}, fresh=True, verified=True, provenance=("r1",))
    conflicting = cache.put(key(), status="pass", result={"ok": False}, fresh=True, verified=True, provenance=("r2",))
    assert conflicting.nondeterministic is True
    assert cache.get(key(), reusable=True) is None
    affected = cache.affected(("h1",), {"h1": ("test/a", "test/a")})
    assert affected["reason_paths"] == [{"handle": "h1", "tests": ["test/a"], "reason": "changed_handle"}]


def test_validation_cache_gc_is_bounded_and_respects_leases() -> None:
    cache = ValidationCache()
    retained = key()
    evictable = ValidationKey(
        "sha256:other", "sha256:lock", "python-3.14", ("pytest",), generation="g2"
    )
    cache.put(retained, status="pass", result={"ok": True})
    cache.put(evictable, status="pass", result={"ok": True})
    cache.acquire_lease(retained, "lease-1")
    plan = cache.gc(keep_generations=("g1",), dry_run=True)
    assert plan["status"] == "planned"
    assert evictable.digest in plan["candidates"]
    assert retained.digest not in plan["candidates"]
    applied = cache.gc(keep_generations=("g1",), dry_run=False)
    assert applied["removed"] == [evictable.digest]
    assert cache.get(retained) is not None
    assert cache.get(evictable) is None
    cache.release_lease(retained, "lease-1")
    with pytest.raises(ValidationCacheError, match="lease_id_invalid"):
        cache.acquire_lease(retained, "bad/lease")
    with pytest.raises(ValidationCacheError, match="gc_budget_invalid"):
        cache.gc(max_entries=0)


def test_validation_cache_serializes_concurrent_publication_and_save(tmp_path) -> None:
    cache = ValidationCache()
    keys = [
        ValidationKey(
            f"sha256:source-{index}", "sha256:lock", "python-3.14", ("pytest",),
            generation=f"g{index}",
        )
        for index in range(24)
    ]

    def publish(item: tuple[int, ValidationKey]) -> None:
        index, item_key = item
        cache.put(item_key, status="pass", result={"index": index})
        cache.acquire_lease(item_key, f"lease-{index}")
        cache.gc(max_entries=1, dry_run=True)
        cache.release_lease(item_key, f"lease-{index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(publish, enumerate(keys)))

    receipt = cache.save(tmp_path / "concurrent-cache.json")
    assert receipt["entries"] == len(keys)
    assert len(ValidationCache.load(tmp_path / "concurrent-cache.json")._entries) == len(keys)
