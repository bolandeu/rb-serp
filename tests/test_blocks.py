"""Команды snippets / ads / related / summary и cache — на настоящих ответах из tests/fixtures."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from rb_serp.core.cache import ResponseCache
from rb_serp.core.models import PageFetch, SearchParams
from rb_serp.core.serp_xml import parse_page

from .conftest import fixture_xml
from .test_cli import invoke, invoke_json


@pytest.fixture
def pages_from(monkeypatch: pytest.MonkeyPatch):
    """Подменить сеть: каждая страница — разобранный ответ из указанного образца."""

    def use(fixture: str, provider: str = "xmlstock") -> list:
        seen: list = []

        def fake_fetch_sync(self, params: SearchParams, *, pages=None, use_cache=False, max_age=None):
            seen.append(pages)
            return [
                PageFetch(
                    query=query, engine=engine, page=page, provider=provider, fetched_at=datetime.now(UTC),
                    from_cache=False, data=parse_page(fixture_xml(fixture)),
                )
                for query in params.get_queries()
                for engine in params.get_engines()
                for page in range(params.num_pages if pages is None else pages)
            ]  # fmt: skip

        monkeypatch.setattr("rb_serp.core.client.SerpClient.fetch_sync", fake_fetch_sync)
        return seen

    return use


def test_snippets_have_all_fields_and_respect_top(pages_from, tmp_path: Path) -> None:
    pages_from("xmlstock_yandex.xml")  # 14 результатов на странице
    code, payload = invoke_json("--project", str(tmp_path), "snippets", "купить диван")
    assert code == 0
    assert payload["count"] == 10
    first = payload["results"][0]
    assert first["serp_position"] == 1
    assert first["snippet"] and first["breadcrumbs"]


def test_ads_first_page_only(pages_from, tmp_path: Path) -> None:
    seen = pages_from("xmlstock_yandex.xml")
    code, payload = invoke_json("--project", str(tmp_path), "ads", "купить диван")
    assert code == 0
    assert seen == [1]  # реклама только на первой странице — лишние не запрашиваем
    assert {r["placement"] for r in payload["results"]} == {"top", "bottom"}
    assert payload["results"][0]["query"] == "купить диван"


def test_related(pages_from, tmp_path: Path) -> None:
    pages_from("xmlstock_google.xml")
    code, payload = invoke_json("--project", str(tmp_path), "related", "купить диван", "-e", "google")
    assert code == 0
    assert {r["kind"] for r in payload["results"]} == {"search", "question"}


def test_summary(pages_from, tmp_path: Path) -> None:
    pages_from("xmlstock_google.xml")
    code, payload = invoke_json("--project", str(tmp_path), "summary", "купить диван", "-e", "google")
    assert code == 0
    row = payload["results"][0]
    assert row["ai_present"] is True
    assert row["related"] > 0


def test_blocks_human_tables(pages_from, tmp_path: Path) -> None:
    pages_from("xmlriver_yandex.xml", provider="xmlriver")
    result = invoke("--project", str(tmp_path), "ads", "купить диван", env={"COLUMNS": "200"})
    assert result.exit_code == 0
    assert "hoff.ru" in result.stdout
    empty = invoke("--project", str(tmp_path), "related", "купить диван")
    assert "Ничего нет" in empty.stdout


# --- cache commands -------------------------------------------------------------


def fill_journal() -> None:
    cache = ResponseCache()
    for query in ("диван", "кресло"):
        cache.put(provider="xmlstock", engine="yandex", region="213", query=query, page=0, xml="<a/>")


def test_cache_info_and_list() -> None:
    fill_journal()
    _, info = invoke_json("cache", "info")
    assert info["entries"] == 2
    assert info["by_provider"] == {"xmlstock": 2}
    _, listing = invoke_json("cache", "list", "--query", "крес")
    assert [r["query"] for r in listing["results"]] == ["кресло"]
    human = invoke("cache", "info")
    assert "Журнал ответов" in human.stdout


def test_cache_clear_needs_one_mode_and_confirmation() -> None:
    fill_journal()
    code, payload = invoke_json("cache", "clear")
    assert code == 2

    code, payload = invoke_json("cache", "clear", "--all", "--dry-run")
    assert (code, payload["would_delete"]) == (0, 2)

    code, payload = invoke_json("--non-interactive", "cache", "clear", "--all")
    assert code == 2  # без --confirm необратимое не выполняется

    code, payload = invoke_json("cache", "clear", "--older-than", "1d", "--confirm")
    assert (code, payload["deleted"]) == (0, 0)
    code, payload = invoke_json("cache", "clear", "--all", "--confirm")
    assert (code, payload["deleted"]) == (0, 2)
    assert "пуст" in invoke("cache", "list").stdout


# --- стоимость у блоков (T175) ----------------------------------------------------

BLOCKS = ["snippets", "ads", "related", "summary"]


@pytest.mark.parametrize("command", BLOCKS)
def test_blocks_live_answer_is_billed_at_price(pages_from, tmp_path: Path, command: str) -> None:
    pages_from("xmlstock_yandex.xml")
    code, payload = invoke_json(
        "--project", str(tmp_path), command, "купить диван", "-p", "xmlstock", env={"SERP_XMLSTOCK_PRICE": "0.3"}
    )
    assert code == 0
    source = payload["sources"][0]
    assert source["billable_requests"] == 1
    assert source["cost"] == 0.3
    assert source["cost_currency"] == "RUB"
    assert source["cost_source"] == "price_table"
    assert payload["billable_requests_total"] == 1
    assert payload["cost_total"] == 0.3


@pytest.mark.parametrize("command", BLOCKS)
def test_blocks_from_journal_cost_nothing(tmp_path: Path, command: str) -> None:
    ResponseCache().put(
        provider="xmlstock", engine="yandex", region="213", query="купить диван", page=0,
        xml=fixture_xml("xmlstock_yandex.xml"),
    )  # fmt: skip
    code, payload = invoke_json(
        "--project", str(tmp_path), command, "купить диван", "-p", "xmlstock", "--cache",
        env={"SERP_XMLSTOCK_PRICE": "0.3"},
    )  # fmt: skip
    assert code == 0
    source = payload["sources"][0]
    assert source["from_cache"] is True
    assert source["billable_requests"] == 0
    assert source["cost"] == 0
    assert source["cost_source"] == "cache"
    assert payload["billable_requests_total"] == 0
    assert payload["cost_total"] == 0


@pytest.mark.parametrize("command", BLOCKS)
def test_blocks_unknown_price_is_null_with_warning(pages_from, tmp_path: Path, command: str, monkeypatch) -> None:
    pages_from("xmlstock_yandex.xml")
    monkeypatch.delenv("SERP_XMLSTOCK_PRICE", raising=False)
    result = invoke("--project", str(tmp_path), command, "купить диван", "-p", "xmlstock", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sources"][0]["cost"] is None
    assert payload["cost_total"] is None
    assert "SERP_XMLSTOCK_PRICE" in result.stderr


@pytest.mark.parametrize("command", BLOCKS)
def test_blocks_help_describes_cost(command: str) -> None:
    result = invoke(command, "--help", env={"COLUMNS": "200"})
    assert "billable_requests_total" in result.output
    assert "cost_total" in result.output
