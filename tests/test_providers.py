from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from rb_serp import AuthRequired, ProviderError, SearchParams, SerpClient
from rb_serp.core.cache import ResponseCache
from rb_serp.core.errors import InvalidArguments
from rb_serp.core.models import PageRequest
from rb_serp.providers import (
    PROVIDERS,
    Health,
    Provider,
    SerpProvider,
    XmlRiverProvider,
    XmlStockProvider,
    all_providers,
)

CREDS = {"user": "u", "token": "t"}


def xml_docs(*urls: str, found: int | None = None) -> str:
    groups = "".join(f"<group><doc><url>{url}</url><title>T {url}</title></doc></group>" for url in urls)
    found_tag = f"<found>{found}</found>" if found is not None else ""
    return (
        f'<?xml version="1.0"?><yandexsearch><response>{found_tag}<results>{groups}</results></response></yandexsearch>'
    )


def urls(n: int, prefix: str = "d") -> tuple[str, ...]:
    return tuple(f"https://{prefix}{i}.ru/" for i in range(n))


def xml_error(code: int, text: str = "oops") -> str:
    return f'<?xml version="1.0"?><yandexsearch><response><error code="{code}">{text}</error></response></yandexsearch>'


def river(handler: Callable[[httpx.Request], httpx.Response]) -> XmlRiverProvider:
    return XmlRiverProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)


def search(provider: SerpProvider, **params) -> list:
    return SerpClient(provider, cache=False).search_sync(SearchParams(query="q", **params))


def test_parses_results_with_positions_and_domains() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.params["key"] == "t"
        assert "additional" not in request.url.params  # с доп. блоками XML River урезает выдачу Яндекса
        return httpx.Response(200, text=xml_docs("https://www.a.ru/x", "https://b.ru/"))

    results = search(river(handler))
    assert [(r.serp_position, r.domain) for r in results] == [(1, "a.ru"), (2, "b.ru")]
    assert results[0].title == "T https://www.a.ru/x"


def test_xmlriver_google_pages_start_at_one() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        params = request.url.params
        assert "start" not in params  # со start XML River отдаёт выдачу не по запросу
        assert params["ads"] == "1" and "rq" in params["additional"]
        return httpx.Response(200, text=xml_docs(f"https://p{params['page']}.ru/"))

    results = search(river(handler), engine="google", top=20)
    assert [r.domain for r in results] == ["p1.ru", "p2.ru"]


def test_highlighted_title_is_read_whole() -> None:
    body = xml_docs("https://a.ru/").replace(
        "<title>T https://a.ru/</title>", "<title><hlword>Растаможка</hlword> авто</title>"
    )
    assert search(river(lambda r: httpx.Response(200, text=body)))[0].title == "Растаможка авто"


def test_no_results_code_is_empty_not_error() -> None:
    assert search(river(lambda r: httpx.Response(200, text=xml_error(15)))) == []


def test_temporary_errors_are_retried() -> None:
    replies = iter([httpx.Response(200, text=xml_error(110)), httpx.Response(503), httpx.Response(200, text="<bad")])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, text=xml_docs("https://ok.ru/")))

    assert [r.domain for r in search(river(handler))] == ["ok.ru"]


def test_transport_error_is_retried_then_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError, match="ConnectError"):
        search(river(handler))


@pytest.mark.parametrize("reply", [httpx.Response(200, text=xml_error(42)), httpx.Response(401)])
def test_auth_failures(reply: httpx.Response) -> None:
    with pytest.raises(AuthRequired):
        search(river(lambda r: reply))


@pytest.mark.parametrize("reply", [httpx.Response(200, text=xml_error(200, "нет денег")), httpx.Response(400)])
def test_fatal_errors_are_not_retried(reply: httpx.Response) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return reply

    with pytest.raises(ProviderError):
        search(river(handler))
    assert calls == 1


def test_xmlstock_yandex_url_and_free_blocks() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://xmlstock.com/yandexlive/xml/")
        p = request.url.params
        assert (p["user"], p["key"], p["lr"], p["ads"], p["related"]) == ("u", "t", "213", "1", "1")
        return httpx.Response(200, text=xml_docs("https://a.ru/"))

    stock = XmlStockProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)
    assert [r.domain for r in search(stock)] == ["a.ru"]


def test_xmlstock_google_uses_own_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://xmlstock.com/google/xml/")
        params = request.url.params
        assert params["lr"] == "1011969"  # Criteria ID идёт в lr, не в loc
        assert "loc" not in params and "start" not in params
        return httpx.Response(200, text=xml_docs(f"https://p{params['page']}.ru/"))

    stock = XmlStockProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)
    assert [r.domain for r in search(stock, engine="google", top=20)] == ["p0.ru", "p1.ru"]


def test_missing_credentials_is_auth_required() -> None:
    with pytest.raises(AuthRequired):
        XmlRiverProvider()


def test_legacy_env_vars_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XMLRIVER_USER", "u")
    monkeypatch.setenv("XMLRIVER_TOKEN", "t")
    assert XmlRiverProvider.load_credential() == CREDS


# --- SerpClient: queue, top, journal/cache -------------------------------------


class _Stub(SerpProvider):
    name = "stub"

    def __init__(self, error: Exception | None, urls: tuple[str, ...], found: int | None) -> None:
        self.error = error
        self.urls = urls
        self.found = found
        self.calls = 0

    async def fetch_page(self, request: PageRequest) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return xml_docs(*(self.urls or (f"https://{self.name}.ru/{request.page}",)), found=self.found)


def Stub(name: str, error: Exception | None = None, urls: tuple[str, ...] = (), found: int | None = None) -> _Stub:
    return type(f"Stub_{name}", (_Stub,), {"name": name})(error, urls, found)


def test_first_provider_wins() -> None:
    first, second = Stub("a"), Stub("b")
    results = SerpClient(providers=[first, second], cache=False).search_sync(SearchParams(query="q"))
    assert results[0].domain == "a.ru"
    assert second.calls == 0


def test_falls_back_to_next_provider() -> None:
    first, second = Stub("a", ProviderError("a", "down")), Stub("b")
    results = SerpClient(providers=[first, second], cache=False).search_sync(SearchParams(query="q"))
    assert results[0].domain == "b.ru"


def test_single_failure_is_reraised_as_is() -> None:
    with pytest.raises(AuthRequired):
        SerpClient(Stub("a", AuthRequired("a")), cache=False).search_sync(SearchParams(query="q"))


def test_all_failed_is_one_provider_error() -> None:
    chain = [Stub("a", AuthRequired("a")), Stub("b", ProviderError("b", "down"))]
    with pytest.raises(ProviderError, match="a\\+b") as excinfo:
        SerpClient(providers=chain, cache=False).search_sync(SearchParams(query="q"))
    assert "down" in excinfo.value.message


def test_unknown_provider_name_fails_upfront() -> None:
    with pytest.raises(InvalidArguments):
        SerpClient(providers=["nope"])


def test_default_chain_without_credentials_is_auth_required() -> None:
    with pytest.raises(AuthRequired):
        SerpClient(cache=False).search_sync(SearchParams(query="q"))


def test_top_trims_and_dedupes_across_pages() -> None:
    urls = tuple(f"https://d{i}.ru/" for i in range(14))  # как XMLStock: 14 на страницу
    results = SerpClient(Stub("s", urls=urls), cache=False).search_sync(SearchParams(query="q", top=20))
    assert len(results) == 14  # вторая страница — те же URL, дубли убраны
    results = SerpClient(Stub("s", urls=urls), cache=False).search_sync(SearchParams(query="q"))
    assert len(results) == 10


def test_every_answer_is_journaled_and_cache_is_opt_in() -> None:
    stub = Stub("a")
    client = SerpClient(stub)  # журнал по умолчанию
    client.search_sync(SearchParams(query="Q"))
    client.search_sync(SearchParams(query="Q"))
    assert stub.calls == 2  # без use_cache — всегда живой запрос
    assert ResponseCache().stats().entries == 2

    again = Stub("a")
    pages = SerpClient(again).fetch_sync(SearchParams(query="q"), use_cache=True)
    assert again.calls == 0
    assert pages[0].from_cache and pages[0].provider == "a"

    pages = SerpClient(again).fetch_sync(SearchParams(query="q"), use_cache=True, max_age=timedelta(0))
    assert again.calls == 1 and not pages[0].from_cache


def test_cache_serves_only_providers_from_queue_in_order() -> None:
    SerpClient(Stub("a")).search_sync(SearchParams(query="q"))
    SerpClient(Stub("b")).search_sync(SearchParams(query="q"))

    other = Stub("c")
    SerpClient(other).fetch_sync(SearchParams(query="q"), use_cache=True)
    assert other.calls == 1  # ответ a/b не выдаётся за ответ c

    pages = SerpClient(providers=[Stub("b"), Stub("a")]).fetch_sync(SearchParams(query="q"), use_cache=True)
    assert (pages[0].from_cache, pages[0].provider) == (True, "b")  # порядок очереди, а не свежесть


def test_incomplete_page_is_retried_then_next_provider() -> None:
    partial, full = Stub("a", urls=urls(2), found=5000), Stub("b", urls=urls(10), found=5000)
    pages = SerpClient(providers=[partial, full]).fetch_sync(SearchParams(query="q"))
    assert partial.calls == 2  # повтор у того же провайдера
    assert (pages[0].provider, pages[0].complete, len(pages[0].data.organic)) == ("b", True, 10)
    assert ResponseCache().stats().incomplete == 2  # неполные — в истории журнала


def test_best_incomplete_page_is_returned_but_not_served_from_cache() -> None:
    only = Stub("a", urls=urls(3), found=5000)
    results = SerpClient(only).search_sync(SearchParams(query="q"))
    assert len(results) == 3  # лучше неполная выдача, чем ошибка
    pages = SerpClient(Stub("a", urls=urls(3), found=5000)).fetch_sync(SearchParams(query="q"), use_cache=True)
    assert not pages[0].from_cache and pages[0].complete is False


def test_few_results_for_rare_query_is_complete() -> None:
    stub = Stub("a", urls=urls(3), found=3)
    pages = SerpClient(stub).fetch_sync(SearchParams(query="q"))
    assert stub.calls == 1 and pages[0].complete


def test_journal_failure_does_not_break_search(tmp_path: Path) -> None:
    (tmp_path / "db.sqlite").mkdir()  # на месте файла базы — каталог: sqlite не откроет
    client = SerpClient(Stub("a"), cache=ResponseCache(tmp_path / "db.sqlite"))
    assert client.search_sync(SearchParams(query="q"))[0].domain == "a.ru"


# --- health_check: бесплатный запрос баланса (auth status --check) ------------------


def with_balance(monkeypatch: pytest.MonkeyPatch, cls: type[XmlRiverProvider], reply: httpx.Response | Exception):
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(cls, "health_transport", httpx.MockTransport(handler))
    return cls(CREDS), seen


def test_xmlriver_health_reads_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, seen = with_balance(monkeypatch, XmlRiverProvider, httpx.Response(200, text="144.575"))
    health = provider.health_check()
    assert (health.status, health.quota and health.quota.remaining, health.quota and health.quota.unit) == (
        "ok",
        144,
        "rub",
    )
    assert seen[0].url.path == "/api/get_balance/" and seen[0].url.params["key"] == "t"


def test_xmlstock_health_reads_balance(monkeypatch: pytest.MonkeyPatch) -> None:
    body = '{"limits":0,"outgo-day":0,"balance":145.62,"days":0}'
    provider, seen = with_balance(monkeypatch, XmlStockProvider, httpx.Response(200, text=body))
    assert provider.health_check().quota.remaining == 145  # type: ignore[union-attr]
    assert seen[0].url.params["do"] == "balance"


@pytest.mark.parametrize(
    ("cls", "reply"),
    [
        (XmlRiverProvider, httpx.Response(200, text=xml_error(42, "Ключ содержит ошибку"))),
        (XmlRiverProvider, httpx.Response(403)),
        (XmlStockProvider, httpx.Response(200, text="ERROR_VERIFICATION_KEY")),
    ],
)
def test_health_rejected_key_is_auth_required(monkeypatch: pytest.MonkeyPatch, cls, reply) -> None:
    provider, _ = with_balance(monkeypatch, cls, reply)
    with pytest.raises(AuthRequired):
        provider.health_check()


@pytest.mark.parametrize("reply", [httpx.Response(502), httpx.Response(200, text="<html>"), httpx.ConnectError("down")])
def test_health_outage_is_provider_error(monkeypatch: pytest.MonkeyPatch, reply) -> None:
    provider, seen = with_balance(monkeypatch, XmlRiverProvider, reply)
    with pytest.raises(ProviderError):
        provider.health_check()
    assert len(seen) == 1  # без повторов


def test_health_empty_balance_is_quota_exhausted(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, _ = with_balance(monkeypatch, XmlRiverProvider, httpx.Response(200, text="0"))
    assert provider.health_check().status == "quota_exhausted"


# --- provider contract: every provider can prove its credential works -------------


@pytest.mark.parametrize("provider_cls", all_providers(), ids=lambda cls: cls.name)
def test_every_provider_implements_health_check(provider_cls: type[Provider], monkeypatch: pytest.MonkeyPatch) -> None:
    assert provider_cls.health_check is not Provider.health_check, (
        f"{provider_cls.__name__} must implement health_check() — `auth status --check` calls it"
    )
    # auth status --check instantiates providers without arguments (only when the credential is present)
    for field in provider_cls.credential_fields:
        monkeypatch.setenv(f"SERP_{provider_cls.name.upper()}_{field.upper()}", "x")
    provider_cls()


def test_all_providers_are_the_registry() -> None:
    names = [cls.name for cls in all_providers()]
    assert len(names) == len(set(names)), f"duplicate provider names: {names}"
    assert sorted(names) == sorted(PROVIDERS)


def test_all_providers_discovers_package_classes_only() -> None:
    class Found(Provider):
        __module__ = "rb_serp.providers.fake"
        name = "found"

        def health_check(self) -> Health:
            return Health()

    class Base(Provider):
        __module__ = "rb_serp.providers.fake"
        abstract = True

    class Elsewhere(Provider):  # defined outside providers/: not discovered
        name = "elsewhere"

    discovered = all_providers()
    assert Found in discovered
    assert Base not in discovered
    assert Elsewhere not in discovered
