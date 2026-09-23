from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rb_serp.core.cache import ResponseCache, default_path


def put(cache: ResponseCache, query: str = "Купить Диван", *, provider: str = "xmlstock", xml: str = "<a/>") -> None:
    cache.put(provider=provider, engine="yandex", region="213", query=query, page=0, xml=xml)


def test_default_path_follows_env(isolated_cache: Path) -> None:
    assert default_path() == isolated_cache


def test_latest_is_newest_and_ignores_case_and_spaces() -> None:
    cache = ResponseCache()
    put(cache, xml="<old/>")
    put(cache, provider="xmlriver", xml="<new/>")
    hit = cache.latest(engine="yandex", region="213", query="  купить   диван ", page=0)
    assert hit is not None
    assert (hit.xml, hit.provider) == ("<new/>", "xmlriver")
    assert cache.latest(engine="yandex", region="2", query="купить диван", page=0) is None  # другой регион
    assert cache.latest(engine="google", region="213", query="купить диван", page=0) is None


def test_max_age_and_clear_older(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ResponseCache()
    put(cache, "старый")
    monkeypatch.setattr(
        "rb_serp.core.cache._cutoff", lambda age: (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    )
    assert cache.latest(engine="yandex", region="213", query="старый", page=0, max_age=timedelta(days=1)) is None
    assert cache.count_older(timedelta(days=1)) == 1
    assert cache.clear(timedelta(days=1)) == 1
    assert cache.stats().entries == 0


def test_stats_entries_and_clear_all() -> None:
    cache = ResponseCache()
    put(cache, "a")
    put(cache, "b", provider="xmlriver")
    stats = cache.stats()
    assert stats.entries == 2
    assert stats.by_provider == {"xmlstock": 1, "xmlriver": 1}
    assert stats.file_size > 0
    assert [e.query for e in cache.entries()] == ["b", "a"]
    assert [e.query for e in cache.entries(query="A")] == ["a"]
    assert cache.clear(None) == 2
