"""serp_v1_results: a row per SERP position of every journal response — written by a search, backfilled by 0002."""

from __future__ import annotations

import asyncio
import logging
import os
import zlib
from collections import defaultdict
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import rb_keys
import sqlalchemy as sa
from alembic import command

from rb_serp import ProviderError, SearchParams, SerpClient
from rb_serp.core import db, tables
from rb_serp.core.client import organic_results
from rb_serp.core.models import PageFetch
from rb_serp.core.normalize import norm_phrase
from rb_serp.core.serp_xml import parse_page
from rb_serp.providers import XmlRiverProvider

from .conftest import fixture_xml
from .test_cli import invoke_json
from .test_providers import CREDS, xml_docs

PG_URL = os.environ.get("TEST_DATABASE_URL")

RIVER_YANDEX = fixture_xml("xmlriver_yandex.xml")  # 10 results
STOCK_YANDEX = fixture_xml("xmlstock_yandex.xml")  # 14 results, 5 of them on RIVER_YANDEX too
RIVER_GOOGLE = fixture_xml("xmlriver_google.xml")  # 10 results, none on STOCK_YANDEX
QUERY = "Купить Диван"

VIEW_COLUMNS = [
    "fetched_at", "provider", "engine", "region", "query", "query_key", "page", "position",
    "url", "url_key", "domain", "domain_key", "title", "complete",
]  # fmt: skip


def river(*answers: str) -> XmlRiverProvider:
    """XML River without the network: page 0 is RIVER_YANDEX, page 1 gives `answers` one by one (the last repeats)."""
    left = list(answers)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "0":
            return httpx.Response(200, text=RIVER_YANDEX)
        return httpx.Response(200, text=left.pop(0) if len(left) > 1 else left[0])

    return XmlRiverProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)


def view_rows(engine: sa.Engine) -> list[sa.RowMapping]:
    v = tables.v1_results.c
    with engine.connect() as conn:
        return list(conn.execute(sa.select(tables.v1_results).order_by(v.fetched_at, v.position)).mappings())


def by_response(rows: list[sa.RowMapping]) -> dict[datetime, list[tuple[int, str, str, str]]]:
    """(position, url, domain, title) per journal response, a response being its fetched_at."""
    grouped: dict[datetime, list[tuple[int, str, str, str]]] = defaultdict(list)
    for row in rows:
        grouped[row.fetched_at].append((row.position, row.url, row.domain, row.title))
    return dict(grouped)


def search_positions(page0: str, page1: str) -> list[tuple[int, str, str, str]]:
    """What `serp search` gives for page 1 when the call used these pages."""
    pages = [
        PageFetch(query=QUERY, engine="yandex", page=number, provider="p", fetched_at=datetime.now(UTC),
                  from_cache=False, data=parse_page(xml))
        for number, xml in ((0, page0), (1, page1))
    ]  # fmt: skip
    return [(r.serp_position, r.url, r.domain, r.title) for r in organic_results(pages, 100) if r.serp_position > 10]


# ── on write ────────────────────────────────────────────────────────────────


def test_search_writes_its_positions() -> None:
    pages = SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))
    rows = view_rows(db.open_db())

    expected = [(r.serp_position, r.url, r.domain, r.title) for r in organic_results(pages, 20)]
    assert [(r.position, r.url, r.domain, r.title) for r in rows] == expected
    assert len(rows) == 19  # 10 on page 0, 14 - 5 duplicates on page 1
    assert [r.page for r in rows] == [0] * 10 + [1] * 9
    assert rows[10].position == 11 and rows[10].url == "https://zamm.ru/catalog/divany/divany_1/"
    for row in rows:
        assert (row.provider, row.engine, row.region, row.query, row.complete) == (
            "xmlriver",
            "yandex",
            "213",
            QUERY,
            True,
        )
        assert row.query_key == "купить диван" == rb_keys.norm_query(QUERY)
        assert row.url_key == rb_keys.norm_url(row.url)
        assert row.domain_key == rb_keys.norm_domain(row.domain)
    assert rows[0].domain_key == "divano.ru"


def test_a_result_that_is_no_url_has_null_keys() -> None:
    SerpClient(river(xml_docs("not a url"))).fetch_sync(SearchParams(query=QUERY, top=20))
    row = view_rows(db.open_db())[-1]
    assert (row.position, row.url, row.url_key, row.domain, row.domain_key) == (11, "not a url", None, "", None)


def test_search_top_is_the_first_positions_of_the_view() -> None:
    results = SerpClient(river(STOCK_YANDEX)).search_sync(SearchParams(query=QUERY, top=10))
    rows = view_rows(db.open_db())
    assert [(r.position, r.url) for r in rows] == [(r.serp_position, r.url) for r in results]
    assert len(rows) == 10


def test_incomplete_page_and_its_retry_both_get_rows() -> None:
    page0 = [item.url for item in parse_page(RIVER_YANDEX).organic]
    short = xml_docs(page0[0], "https://new1.ru/", "https://new2.ru/", found=100_000)  # incomplete: 3 of plenty
    SerpClient(river(short, STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))

    engine = db.open_db()
    responses = by_response(view_rows(engine))
    with engine.connect() as conn:
        complete = dict(conn.execute(sa.select(tables.v1_responses.c.fetched_at, tables.v1_responses.c.complete)).all())
    assert len(responses) == 3  # page 0, the incomplete page 1, its complete retry
    incomplete = [rows for fetched_at, rows in responses.items() if not complete[fetched_at]]
    assert incomplete == [[(11, "https://new1.ru/", "new1.ru", "T https://new1.ru/"),
                           (12, "https://new2.ru/", "new2.ru", "T https://new2.ru/")]]  # fmt: skip
    retry = [rows for fetched_at, rows in responses.items() if complete[fetched_at] and rows[0][0] > 10]
    assert retry == [search_positions(RIVER_YANDEX, STOCK_YANDEX)]


def test_failed_page_keeps_the_rows_of_paid_pages() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["page"] == "0":
            return httpx.Response(200, text=RIVER_YANDEX)
        await asyncio.sleep(0.05)  # fails once page 0 is paid and journaled
        return httpx.Response(400)

    provider = XmlRiverProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)
    with pytest.raises(ProviderError):
        SerpClient(provider).fetch_sync(SearchParams(query=QUERY, top=20))
    assert [r.page for r in view_rows(db.open_db())] == [0] * 10


def test_cached_pages_get_no_new_rows() -> None:
    SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))
    SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20), use_cache=True)
    assert len(view_rows(db.open_db())) == 19


def test_no_journal_no_rows() -> None:
    SerpClient(river(STOCK_YANDEX), cache=False).fetch_sync(SearchParams(query=QUERY, top=20))
    assert view_rows(db.open_db()) == []


# ── cache clear ─────────────────────────────────────────────────────────────


def test_cache_clear_deletes_the_rows_of_old_responses() -> None:
    engine = db.open_db()
    SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))
    old = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as conn:  # the first search was long ago: each response and its rows move together
        fetched = sorted(conn.execute(sa.select(tables.responses.c.fetched_at)).scalars())
        for fetched_at in fetched:
            moved = old + (fetched_at - fetched[0])
            for table in (tables.responses, tables.results):
                conn.execute(sa.update(table).where(table.c.fetched_at == fetched_at).values(fetched_at=moved))
    SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))
    assert len(view_rows(engine)) == 38

    code, payload = invoke_json("cache", "clear", "--older-than", "30d", "--confirm", "--non-interactive")
    assert (code, payload["deleted"]) == (0, 2)
    rows = view_rows(engine)
    assert len(rows) == 19
    assert all(row.fetched_at > old + timedelta(days=1) for row in rows)


# ── backfill (migration 0002) ───────────────────────────────────────────────


def _at(day: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 9, day, 10, minute, second, tzinfo=UTC)


# (fetched_at, provider, page, complete, xml): the journal of the old release
SHORT = xml_docs("https://www.divano.ru/catalog/sale/", "https://new1.ru/", "https://new2.ru/", found=100_000)
JOURNAL = [
    (_at(1, 0), "xmlriver", 0, True, RIVER_YANDEX),  # call 1
    (_at(1, 0, 5), "xmlriver", 1, True, STOCK_YANDEX),
    (_at(2, 0), "xmlriver", 0, True, RIVER_YANDEX),  # call 2, the next day
    (_at(2, 0, 5), "xmlriver", 1, False, SHORT),  # incomplete, then a fallback to xmlstock
    (_at(2, 0, 10), "xmlstock", 1, True, STOCK_YANDEX),
    (_at(2, 2), "xmlstock", 0, True, RIVER_GOOGLE),  # call 3: page 0 is already complete in call 2
    (_at(2, 2, 5), "xmlstock", 1, True, STOCK_YANDEX),
    (_at(3, 0), "xmlriver", 0, True, None),  # broken XML
    (_at(4, 0), "xmlriver", 1, True, STOCK_YANDEX),  # call 4: no page 0 → call 3's, the latest complete
]


def first_page(xml: str) -> list[tuple[int, str, str, str]]:
    """Page 0 has no page before it: its results in order (none of the fixtures repeats a URL on one page)."""
    return [(i, item.url, item.domain, item.title) for i, item in enumerate(parse_page(xml).organic, 1)]


def _journal_at_0001(engine: sa.Engine) -> None:
    with engine.begin() as conn:
        command.upgrade(db.alembic_config(conn), "0001")
        conn.execute(
            sa.insert(tables.responses),
            [
                {"fetched_at": fetched_at, "provider": provider, "engine": "yandex", "region": "213", "query": QUERY,
                 "query_key": norm_phrase(QUERY), "page": page, "complete": complete,
                 "xml": zlib.compress(xml.encode()) if xml else b"x"}
                for fetched_at, provider, page, complete, xml in JOURNAL
            ],
        )  # fmt: skip


def _assert_backfilled(engine: sa.Engine) -> None:
    responses = by_response(view_rows(engine))
    river_yandex, river_google = first_page(RIVER_YANDEX), first_page(RIVER_GOOGLE)
    assert responses == {
        _at(1, 0): river_yandex,
        _at(1, 0, 5): search_positions(RIVER_YANDEX, STOCK_YANDEX),
        _at(2, 0): river_yandex,
        _at(2, 0, 5): [(11, "https://new1.ru/", "new1.ru", "T https://new1.ru/"),
                       (12, "https://new2.ru/", "new2.ru", "T https://new2.ru/")],
        _at(2, 0, 10): search_positions(RIVER_YANDEX, STOCK_YANDEX),
        _at(2, 2): river_google,
        _at(2, 2, 5): search_positions(RIVER_GOOGLE, STOCK_YANDEX),
        _at(4, 0): search_positions(RIVER_GOOGLE, STOCK_YANDEX),
    }  # fmt: skip
    assert len(responses[_at(1, 0, 5)]) == 9 and len(responses[_at(2, 2, 5)]) == 14
    assert {row.query_key for row in view_rows(engine)} == {"купить диван"}


def test_migration_backfills_the_journal(caplog: pytest.LogCaptureFixture) -> None:
    engine = db.sqlite_engine(db.sqlite_path())
    _journal_at_0001(engine)
    with caplog.at_level(logging.WARNING):
        db.upgrade(engine)
    _assert_backfilled(engine)
    assert [column["name"] for column in sa.inspect(engine).get_columns("serp_v1_results")] == VIEW_COLUMNS
    assert any("2026-09-03" in record.getMessage() for record in caplog.records)  # the broken one is named


# ── PostgreSQL ──────────────────────────────────────────────────────────────


@pytest.mark.skipif(not PG_URL, reason="TEST_DATABASE_URL not set")
def test_postgres_view_backfill_and_write(monkeypatch: pytest.MonkeyPatch) -> None:
    assert PG_URL
    assert sa.make_url(PG_URL).host in ("127.0.0.1", "localhost"), "TEST_DATABASE_URL must be scripts/pg_test.sh's"
    monkeypatch.setenv(db.ENV_VAR, PG_URL)
    engine = db.open_db(check=False)
    with engine.begin() as conn:  # a clean slate: only this tool's objects
        conn.execute(sa.text("DROP VIEW IF EXISTS serp_v1_results, serp_v1_responses"))
        conn.execute(sa.text(f"DROP TABLE IF EXISTS serp_results, serp_responses, {db.VERSION_TABLE}"))
    _journal_at_0001(engine)
    db.upgrade(engine)

    columns = [column["name"] for column in sa.inspect(engine).get_columns("serp_v1_results")]
    assert columns == VIEW_COLUMNS
    _assert_backfilled(engine)

    before = len(view_rows(engine))
    SerpClient(river(STOCK_YANDEX)).fetch_sync(SearchParams(query=QUERY, top=20))
    assert len(view_rows(engine)) == before + 19
    engine.dispose()
