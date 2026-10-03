"""Платные вызовы провайдера (T175): каждый успешный ответ — платный, сбои и ответы-ошибки — нет."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx

from rb_serp import ProviderError, SearchParams, SerpClient
from rb_serp.core import services
from rb_serp.core.models import PageData, PageFetch

from .test_providers import Stub, river, urls, xml_docs, xml_error


def fetch(*providers) -> PageFetch:
    return SerpClient(providers=list(providers), cache=False).fetch_sync(SearchParams(query="q"))[0]


def test_complete_page_is_one_billable_request() -> None:
    assert fetch(Stub("a")).billable == {"a": 1}


def test_incomplete_page_retry_bills_both_answers() -> None:
    only = Stub("a", urls=urls(3), found=5000)
    page = fetch(only)
    assert only.calls == 2 and page.complete is False
    assert page.billable == {"a": 2}


def test_fallback_after_incomplete_bills_each_provider() -> None:
    page = fetch(Stub("a", urls=urls(2), found=5000), Stub("b", urls=urls(10), found=5000))
    assert page.provider == "b"
    assert page.billable == {"a": 2, "b": 1}


def test_provider_error_is_not_billed() -> None:
    page = fetch(Stub("a", ProviderError("a", "down")), Stub("b"))
    assert page.billable == {"b": 1}


def test_no_results_answer_is_billed() -> None:
    page = fetch(river(lambda r: httpx.Response(200, text=xml_error(15))))
    assert page.data.organic == []
    assert page.billable == {"xmlriver": 1}


def test_retried_error_codes_are_not_billed() -> None:
    replies = iter([httpx.Response(200, text=xml_error(110)), httpx.Response(503)])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, text=xml_docs("https://ok.ru/")))

    assert fetch(river(handler)).billable == {"xmlriver": 1}


def _page(provider: str, billable: dict[str, int], *, from_cache: bool = False) -> PageFetch:
    return PageFetch(
        query="q", engine="yandex", page=0, provider=provider, fetched_at=datetime.now(UTC),
        from_cache=from_cache, billable=billable, data=PageData(),
    )  # fmt: skip


def test_cost_sums_each_provider_by_its_price() -> None:
    rows, total = services.priced_sources([_page("b", {"a": 2, "b": 1})], {"a": 0.1, "b": 0.5})
    assert rows[0]["billable_requests"] == 3
    assert rows[0]["cost"] == 0.7
    assert rows[0]["cost_source"] == "price_table"
    assert total == 0.7


def test_cost_is_null_when_any_billed_provider_has_no_price() -> None:
    rows, total = services.priced_sources([_page("b", {"a": 2, "b": 1})], {"a": None, "b": 0.5})
    assert rows[0]["billable_requests"] == 3
    assert rows[0]["cost"] is None and rows[0]["cost_source"] is None
    assert total is None


def test_billed_providers_need_prices() -> None:
    page = _page("b", {"a": 2, "b": 1})
    assert services.billed_providers([page]) == ["a", "b"]
    assert services.billed_providers([_page("a", {}, from_cache=True)]) == []
