from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rb_serp.core.cache import ResponseCache, default_path


def put(
    cache: ResponseCache,
    query: str = "Купить Диван",
    *,
    provider: str = "xmlstock",
    xml: str = "<a/>",
    complete: bool = True,
) -> None:
    cache.put(provider=provider, engine="yandex", region="213", query=query, page=0, xml=xml, complete=complete)


def latest(cache: ResponseCache, query: str = "купить диван", *, provider: str = "xmlstock", **key):
    return cache.latest(
        provider=provider,
        engine=key.get("engine", "yandex"),
        region=key.get("region", "213"),
        query=query,
        page=0,
        max_age=key.get("max_age"),
    )


def test_default_path_follows_env(isolated_cache: Path) -> None:
    assert default_path() == isolated_cache


def test_latest_is_newest_and_ignores_case_and_spaces() -> None:
    cache = ResponseCache()
    put(cache, xml="<old/>")
    put(cache, xml="<new/>")
    hit = latest(cache, "  купить   ДИВАН ")
    assert hit is not None
    assert hit.xml == "<new/>"
    assert latest(cache, region="2") is None  # другой регион
    assert latest(cache, engine="google") is None


def test_latest_is_per_provider() -> None:
    cache = ResponseCache()
    put(cache, provider="xmlstock", xml="<stock/>")
    assert latest(cache, provider="xmlriver") is None  # ответ XMLStock не подменяет XML River
    put(cache, provider="xmlriver", xml="<river/>")
    hit = latest(cache, provider="xmlriver")
    assert hit is not None and hit.xml == "<river/>"


def test_incomplete_is_kept_but_never_served() -> None:
    cache = ResponseCache()
    put(cache, xml="<full/>")
    put(cache, xml="<partial/>", complete=False)  # новее, но неполная
    hit = latest(cache)
    assert hit is not None and hit.xml == "<full/>"
    assert cache.stats().incomplete == 1
    assert [e.complete for e in cache.entries()] == [False, True]


def test_old_database_is_migrated(isolated_cache: Path) -> None:
    import sqlite3
    import zlib

    from .conftest import fixture_xml

    isolated_cache.parent.mkdir(parents=True)
    conn = sqlite3.connect(isolated_cache)
    conn.execute(  # схема 0.2.x — без complete
        "CREATE TABLE responses (id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, provider TEXT NOT NULL,"
        " engine TEXT NOT NULL, region TEXT NOT NULL, query TEXT NOT NULL, query_key TEXT NOT NULL,"
        " page INTEGER NOT NULL, xml BLOB NOT NULL)"
    )
    partial = "<yandexsearch><response><found>5000</found><results><grouping><group><doc><url>https://a.ru/</url></doc></group></grouping></results></response></yandexsearch>"
    for xml in (fixture_xml("xmlstock_yandex.xml"), partial):
        conn.execute(
            "INSERT INTO responses (fetched_at, provider, engine, region, query, query_key, page, xml)"
            " VALUES (?, 'xmlriver', 'yandex', '213', 'q', 'q', 0, ?)",
            (datetime.now(UTC).isoformat(), zlib.compress(xml.encode())),
        )
    conn.commit()
    conn.close()

    assert [e.complete for e in ResponseCache().entries()] == [False, True]


def test_max_age_and_clear_older(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ResponseCache()
    put(cache, "старый")
    monkeypatch.setattr(
        "rb_serp.core.cache._cutoff", lambda age: (datetime.now(UTC) + timedelta(seconds=1)).isoformat()
    )
    assert latest(cache, "старый", max_age=timedelta(days=1)) is None
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


def test_help_line_shows_size_without_creating_db(isolated_cache: Path) -> None:
    from rb_serp.core.cache import help_line, human_size

    assert "пока пуст" in help_line()
    assert not isolated_cache.exists()  # справка не создаёт базу
    put(ResponseCache())
    assert "КБ" in help_line()
    assert human_size(512) == "512 Б" and human_size(3 * 1024**3) == "3.0 ГБ"
