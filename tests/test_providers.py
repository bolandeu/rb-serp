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
from rb_serp.providers import SerpProvider, XmlRiverProvider, XmlStockProvider

CREDS = {"user": "u", "token": "t"}


def xml_docs(*urls: str) -> str:
    groups = "".join(f"<group><doc><url>{url}</url><title>T {url}</title></doc></group>" for url in urls)
    return f'<?xml version="1.0"?><yandexsearch><response><results>{groups}</results></response></yandexsearch>'


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

    def __init__(self, error: Exception | None, urls: tuple[str, ...]) -> None:
        self.error = error
        self.urls = urls
        self.calls = 0

    async def fetch_page(self, request: PageRequest) -> str:
        self.calls += 1
        if self.error:
            raise self.error
        return xml_docs(*(self.urls or (f"https://{self.name}.ru/{request.page}",)))


def Stub(name: str, error: Exception | None = None, urls: tuple[str, ...] = ()) -> _Stub:
    return type(f"Stub_{name}", (_Stub,), {"name": name})(error, urls)


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

    other = Stub("b")
    pages = SerpClient(other).fetch_sync(SearchParams(query="q"), use_cache=True)  # провайдер не в ключе
    assert other.calls == 0
    assert pages[0].from_cache and pages[0].provider == "a"

    pages = SerpClient(other).fetch_sync(SearchParams(query="q"), use_cache=True, max_age=timedelta(0))
    assert other.calls == 1 and not pages[0].from_cache


def test_journal_failure_does_not_break_search(tmp_path: Path) -> None:
    (tmp_path / "db.sqlite").mkdir()  # на месте файла базы — каталог: sqlite не откроет
    client = SerpClient(Stub("a"), cache=ResponseCache(tmp_path / "db.sqlite"))
    assert client.search_sync(SearchParams(query="q"))[0].domain == "a.ru"
