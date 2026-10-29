from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa

from rb_serp.core import db
from rb_serp.core.cache import ResponseCache
from rb_serp.core.tables import responses


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


def test_journal_is_the_tool_database(_local_database: Path) -> None:
    put(ResponseCache())
    assert _local_database.is_file()
    assert ResponseCache().stats().entries == 1


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


def _legacy_db(path: Path, *, with_complete: bool) -> None:
    """Журнал serp <= 0.4: таблица responses, даты ISO с поправкой, без Alembic."""
    import sqlite3
    import zlib

    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE responses (id INTEGER PRIMARY KEY, fetched_at TEXT NOT NULL, provider TEXT NOT NULL,"
        " engine TEXT NOT NULL, region TEXT NOT NULL, query TEXT NOT NULL, query_key TEXT NOT NULL,"
        " page INTEGER NOT NULL, xml BLOB NOT NULL"
        + (", complete INTEGER NOT NULL DEFAULT 1)" if with_complete else ")")
    )
    conn.execute("CREATE INDEX responses_date ON responses (fetched_at)")
    rows = [("2026-09-23T13:34:02.673606+00:00", "Купить  Ёлку", 1), ("2026-09-28T19:41:25+00:00", "q", 0)]
    for fetched_at, query, complete in rows:
        columns, values = (
            "fetched_at, provider, engine, region, query, query_key, page, xml",
            "?, 'xmlriver', 'yandex', '213', ?, ?, 0, ?",
        )
        args = [fetched_at, query, query.lower(), zlib.compress(b"<a/>")]
        if with_complete:
            columns, values, args = columns + ", complete", values + ", ?", [*args, complete]
        conn.execute(f"INSERT INTO responses ({columns}) VALUES ({values})", args)  # noqa: S608
    conn.commit()
    conn.close()


def test_legacy_journal_moves_to_serp_responses_with_its_dates(_local_database: Path) -> None:
    _legacy_db(_local_database, with_complete=True)
    entries = ResponseCache().entries()
    assert [(e.fetched_at, e.query, e.complete) for e in entries] == [
        (datetime(2026, 9, 28, 19, 41, 25, tzinfo=UTC), "q", False),
        (datetime(2026, 9, 23, 13, 34, 2, 673606, tzinfo=UTC), "Купить  Ёлку", True),
    ]
    hit = latest(ResponseCache(), "купить елку", provider="xmlriver")  # ключ пересчитан: ё→е
    assert hit is not None and hit.fetched_at == datetime(2026, 9, 23, 13, 34, 2, 673606, tzinfo=UTC)
    engine = db.open_db()
    names = sa.inspect(engine).get_table_names()
    assert "responses" not in names and "serp_responses" in names
    assert sa.inspect(engine).get_view_names() == ["serp_v1_responses", "serp_v1_results"]


def test_journal_before_0_3_counts_as_complete(_local_database: Path) -> None:
    _legacy_db(_local_database, with_complete=False)
    assert [e.complete for e in ResponseCache().entries()] == [True, True]


def test_max_age_and_clear_older(monkeypatch: pytest.MonkeyPatch) -> None:
    cache = ResponseCache()
    put(cache, "старый")
    monkeypatch.setattr("rb_serp.core.cache._cutoff", lambda age: datetime.now(UTC) + timedelta(seconds=1))
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


def test_help_line_shows_size_without_creating_db(_local_database: Path) -> None:
    from rb_serp.core.cache import help_line, human_size

    assert "пока пуст" in help_line()
    assert not _local_database.exists()  # справка не создаёт базу
    put(ResponseCache())
    assert "КБ" in help_line()
    assert human_size(512) == "512 Б" and human_size(3 * 1024**3) == "3.0 ГБ"


def test_help_line_names_the_shared_database_without_password(monkeypatch: pytest.MonkeyPatch) -> None:
    from rb_serp.core.cache import help_line

    monkeypatch.setenv(db.ENV_VAR, "postgresql://serp:secret@homeserver:5432/directus")
    assert "homeserver" in help_line() and "secret" not in help_line()


def _raw_row(cache: ResponseCache, xml: bytes, fetched_at: datetime, *, complete: bool = True) -> None:
    """Строка журнала с произвольными байтами в xml — как будто записал не `put`."""
    with cache.engine.begin() as conn:
        conn.execute(
            sa.insert(responses).values(
                fetched_at=fetched_at, provider="xmlstock", engine="yandex", region="213", query="Купить Диван",
                query_key="купить диван", page=0, complete=complete, xml=xml,
            )
        )  # fmt: skip


def test_latest_skips_a_row_that_is_not_zlib_and_uses_the_older_one(caplog: pytest.LogCaptureFixture) -> None:
    cache = ResponseCache()
    put(cache, xml="<older/>")
    _raw_row(cache, b"garbage", datetime.now(UTC) + timedelta(seconds=5))
    hit = latest(cache)
    assert hit is not None and hit.xml == "<older/>"
    assert "garbage" not in caplog.text
    assert len([r for r in caplog.records if r.levelname == "WARNING"]) == 1


def test_latest_is_a_miss_when_the_only_row_is_corrupt() -> None:
    cache = ResponseCache()
    _raw_row(cache, b"garbage", datetime.now(UTC))
    assert latest(cache) is None


def test_latest_accepts_a_plain_xml_row() -> None:
    cache = ResponseCache()
    _raw_row(cache, b"\xef\xbb\xbf \n<yandexsearch>\xd0\x94</yandexsearch>", datetime.now(UTC))
    hit = latest(cache)
    assert hit is not None and hit.xml.endswith("<yandexsearch>Д</yandexsearch>")
