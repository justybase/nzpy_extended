from __future__ import annotations

import pytest

from nzpy_extended._dbos import _ValueCache


@pytest.mark.unit
def test_value_cache_returns_the_same_immutable_object() -> None:
    cache = _ValueCache(max_entries=16, max_key=64, probe=100)
    first = cache.get_or_create(b"alpha", lambda: "alpha")
    second = cache.get_or_create(b"alpha", lambda: pytest.fail("decoded a cached value"))
    assert second is first


@pytest.mark.unit
def test_value_cache_is_bounded_and_disables_high_cardinality_columns() -> None:
    cache = _ValueCache(max_entries=8, max_key=64, probe=8)
    for i in range(8):
        cache.get_or_create(str(i).encode(), lambda i=i: str(i))
    assert not cache._enabled
    assert not cache._entries


@pytest.mark.unit
def test_value_cache_skips_long_keys_but_keeps_last_hit() -> None:
    cache = _ValueCache(max_entries=8, max_key=4, probe=100)
    first = cache.get_or_create(b"abcdef", lambda: "abcdef")
    second = cache.get_or_create(b"abcdef", lambda: "abcdef")
    assert second is first
    assert not cache._entries
