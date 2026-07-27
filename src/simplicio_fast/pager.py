"""Bounded working-set pager reference for Fast semantic pages."""

from __future__ import annotations

import hashlib
import threading
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Iterable


SCHEMA = "simplicio.fast.semantic-pager/v1"


class PagerError(ValueError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class PageKey:
    repository: str
    generation: str
    overlay: str | None
    segment: str
    logical_range: str
    schema: str = SCHEMA


@dataclass(slots=True)
class _Page:
    key: PageKey
    data: bytes
    digest: str
    last_touch: int
    leases: int = 0
    invalidated: bool = False


@dataclass(slots=True)
class _Flight:
    event: threading.Event
    error: BaseException | None = None


class PageLease(AbstractContextManager[bytes]):
    def __init__(self, pager: "SemanticPager", key: PageKey, data: bytes) -> None:
        self._pager = pager
        self.key = key
        self.data = data
        self._closed = False

    def __enter__(self) -> bytes:
        return self.data

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.close()

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._pager.release(self.key)


class SemanticPager:
    """Thread-safe, generation-scoped bounded page cache."""

    def __init__(self, repository: str, generation: str, *, max_bytes: int, max_pages: int) -> None:
        if max_bytes < 1 or max_pages < 1:
            raise ValueError("max_bytes and max_pages must be positive")
        self.repository = repository
        self.generation = generation
        self.max_bytes = max_bytes
        self.max_pages = max_pages
        self._pages: dict[PageKey, _Page] = {}
        self._flights: dict[PageKey, _Flight] = {}
        self._lock = threading.RLock()
        self._clock = 0
        self._bytes = 0
        self._metrics = {
            "hits": 0,
            "misses": 0,
            "waits": 0,
            "evictions": 0,
            "invalidations": 0,
            "bytes_mapped": 0,
            "bytes_touched": 0,
            "bytes_copied": 0,
            "prefetch_useful": 0,
            "prefetch_wasted": 0,
        }

    def _check_key(self, key: PageKey) -> None:
        if key.repository != self.repository:
            raise PagerError("cross_repo_page", "page belongs to another repository")
        if key.generation != self.generation:
            raise PagerError("stale_generation", "page belongs to another generation")
        if key.schema != SCHEMA:
            raise PagerError("schema_mismatch", "unsupported page schema")

    def _touch(self, page: _Page) -> None:
        self._clock += 1
        page.last_touch = self._clock
        self._metrics["bytes_touched"] += len(page.data)

    def _evict(self, required: int = 0) -> None:
        while self._bytes + required > self.max_bytes or len(self._pages) >= self.max_pages:
            candidates = [page for page in self._pages.values() if page.leases == 0]
            if not candidates:
                raise PagerError("budget_exhausted", "all resident pages are leased")
            victim = min(candidates, key=lambda page: (page.last_touch, page.key.segment, page.key.logical_range))
            self._pages.pop(victim.key)
            self._bytes -= len(victim.data)
            self._metrics["evictions"] += 1

    def get(
        self,
        key: PageKey,
        loader: Callable[[], bytes] | None = None,
        *,
        expected_sha256: str | None = None,
    ) -> bytes:
        self._check_key(key)
        owner = False
        with self._lock:
            page = self._pages.get(key)
            if page is not None and not page.invalidated:
                if expected_sha256 is not None and page.digest != expected_sha256:
                    page.invalidated = True
                    self._metrics["invalidations"] += 1
                    raise PagerError("page_digest_mismatch", "resident page digest differs from expected")
                self._metrics["hits"] += 1
                self._touch(page)
                return page.data
            if loader is None:
                raise PagerError("page_not_resident", "a loader is required for a cold page")
            flight = self._flights.get(key)
            if flight is None:
                flight = _Flight(threading.Event())
                self._flights[key] = flight
                self._metrics["misses"] += 1
                owner = True
            else:
                self._metrics["waits"] += 1
        if not owner:
            flight.event.wait()
            if flight.error is not None:
                raise flight.error
            return self.get(key, loader, expected_sha256=expected_sha256)
        try:
            data = loader()
            if not isinstance(data, bytes):
                raise TypeError("page loader must return bytes")
            digest = hashlib.sha256(data).hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise PagerError("page_digest_mismatch", "loaded page digest differs from expected")
            with self._lock:
                self._evict(len(data))
                self._clock += 1
                page = _Page(key, data, digest, self._clock)
                self._pages[key] = page
                self._bytes += len(data)
                self._metrics["bytes_mapped"] += len(data)
                self._metrics["bytes_copied"] += len(data)
            return data
        except BaseException as error:
            flight.error = error
            raise
        finally:
            with self._lock:
                self._flights.pop(key, None)
                flight.event.set()

    def lease(
        self,
        key: PageKey,
        loader: Callable[[], bytes] | None = None,
        *,
        expected_sha256: str | None = None,
    ) -> PageLease:
        data = self.get(key, loader, expected_sha256=expected_sha256)
        with self._lock:
            self._pages[key].leases += 1
        return PageLease(self, key, data)

    def release(self, key: PageKey) -> None:
        with self._lock:
            page = self._pages.get(key)
            if page is None or page.leases < 1:
                raise PagerError("lease_mismatch", "page lease was not held")
            page.leases -= 1
            if page.invalidated and page.leases == 0:
                self._pages.pop(key, None)
                self._bytes -= len(page.data)

    def invalidate(self, keys: Iterable[PageKey]) -> dict[str, int]:
        removed = 0
        held = 0
        with self._lock:
            for key in keys:
                self._check_key(key)
                page = self._pages.get(key)
                if page is None:
                    continue
                page.invalidated = True
                self._metrics["invalidations"] += 1
                if page.leases:
                    held += 1
                else:
                    self._pages.pop(key)
                    self._bytes -= len(page.data)
                    removed += 1
        return {"removed": removed, "held": held}

    def prefetch(self, keys: Iterable[PageKey], loader: Callable[[PageKey], bytes]) -> dict[str, int]:
        useful = wasted = 0
        for key in keys:
            try:
                with self._lock:
                    resident = key in self._pages and not self._pages[key].invalidated
                self.get(key, lambda key=key: loader(key))
                if resident:
                    wasted += 1
                else:
                    useful += 1
            except PagerError:
                wasted += 1
        self._metrics["prefetch_useful"] += useful
        self._metrics["prefetch_wasted"] += wasted
        return {"useful": useful, "wasted": wasted}

    def stats(self) -> dict[str, object]:
        with self._lock:
            return {
                "schema": SCHEMA,
                "repository": self.repository,
                "generation": self.generation,
                "max_bytes": self.max_bytes,
                "max_pages": self.max_pages,
                "resident_pages": len(self._pages),
                "resident_bytes": self._bytes,
                "held_pages": sum(1 for page in self._pages.values() if page.leases),
                **self._metrics,
            }
