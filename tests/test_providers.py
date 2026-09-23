from __future__ import annotations

from collections.abc import Callable

import httpx
import pytest

from rb_serp import AuthRequired, ProviderError, SearchParams, SerpClient, SerpResult
from rb_serp.core.errors import InvalidArguments
from rb_serp.providers import SerpProvider, XmlRiverProvider, XmlStockProvider

CREDS = {"user": "u", "token": "t"}


def xml_docs(*urls: str) -> str:
    groups = "".join(f"<group><doc><url>{url}</url><title>T {url}</title></doc></group>" for url in urls)
    return f'<?xml version="1.0"?><yandexsearch><response><results>{groups}</results></response></yandexsearch>'


def xml_error(code: int, text: str = "oops") -> str:
    return f'<?xml version="1.0"?><yandexsearch><response><error code="{code}">{text}</error></response></yandexsearch>'


def provider(handler: Callable[[httpx.Request], httpx.Response]) -> XmlRiverProvider:
    return XmlRiverProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)


async def test_parses_results_with_positions_and_domains() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.scheme == "https"
        assert request.url.params["key"] == "t"
        return httpx.Response(200, text=xml_docs("https://www.a.ru/x", "https://b.ru/"))

    results = await provider(handler).search(SearchParams(query="q"))
    assert [(r.serp_position, r.domain) for r in results] == [(1, "a.ru"), (2, "b.ru")]
    assert results[0].title == "T https://www.a.ru/x"


async def test_google_pages_are_offset_and_ordered() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        start = int(request.url.params["start"])
        return httpx.Response(200, text=xml_docs(f"https://p{start}.ru/"))

    results = await provider(handler).search(SearchParams(query="q", engine="google", top=20))
    assert [r.domain for r in results] == ["p0.ru", "p10.ru"]
    assert [r.serp_position for r in results] == [1, 2]


async def test_no_results_code_is_empty_not_error() -> None:
    results = await provider(lambda r: httpx.Response(200, text=xml_error(15))).search(SearchParams(query="q"))
    assert results == []


async def test_temporary_errors_are_retried() -> None:
    replies = iter([httpx.Response(200, text=xml_error(110)), httpx.Response(503), httpx.Response(200, text="<bad")])

    def handler(request: httpx.Request) -> httpx.Response:
        return next(replies, httpx.Response(200, text=xml_docs("https://ok.ru/")))

    results = await provider(handler).search(SearchParams(query="q"))
    assert [r.domain for r in results] == ["ok.ru"]


async def test_transport_error_is_retried_then_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    with pytest.raises(ProviderError, match="ConnectError"):
        await provider(handler).search(SearchParams(query="q"))


@pytest.mark.parametrize("reply", [httpx.Response(200, text=xml_error(42)), httpx.Response(401)])
async def test_auth_failures(reply: httpx.Response) -> None:
    with pytest.raises(AuthRequired):
        await provider(lambda r: reply).search(SearchParams(query="q"))


@pytest.mark.parametrize("reply", [httpx.Response(200, text=xml_error(200, "нет денег")), httpx.Response(400)])
async def test_fatal_errors_are_not_retried(reply: httpx.Response) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return reply

    with pytest.raises(ProviderError):
        await provider(handler).search(SearchParams(query="q"))
    assert calls == 1


async def test_highlighted_title_is_read_whole() -> None:
    body = xml_docs("https://a.ru/").replace(
        "<title>T https://a.ru/</title>", "<title><hlword>Растаможка</hlword> авто</title>"
    )
    results = await provider(lambda r: httpx.Response(200, text=body)).search(SearchParams(query="q"))
    assert results[0].title == "Растаможка авто"


async def test_xmlstock_uses_own_url_and_same_format() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://xmlstock.com/yandexlive/xml/")
        assert (request.url.params["user"], request.url.params["key"], request.url.params["lr"]) == ("u", "t", "213")
        return httpx.Response(200, text=xml_docs("https://a.ru/"))

    stock = XmlStockProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)
    results = await stock.search(SearchParams(query="q"))
    assert [r.domain for r in results] == ["a.ru"]


async def test_xmlstock_google_uses_own_params() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith("https://xmlstock.com/google/xml/")
        params = request.url.params
        assert params["lr"] == "1011969"  # Criteria ID идёт в lr, не в loc
        assert "loc" not in params and "start" not in params
        return httpx.Response(200, text=xml_docs(f"https://p{params['page']}.ru/"))

    stock = XmlStockProvider(CREDS, transport=httpx.MockTransport(handler), retry_delay=0)
    results = await stock.search(SearchParams(query="q", engine="google", top=20))
    assert [r.domain for r in results] == ["p0.ru", "p1.ru"]


def test_missing_credentials_is_auth_required() -> None:
    with pytest.raises(AuthRequired):
        XmlRiverProvider()


def test_legacy_env_vars_are_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XMLRIVER_USER", "u")
    monkeypatch.setenv("XMLRIVER_TOKEN", "t")
    assert XmlRiverProvider.load_credential() == CREDS


# --- SerpClient: provider queue ------------------------------------------------


class _Stub(SerpProvider):
    name = "stub"

    def __init__(self, error: Exception | None) -> None:
        self.error = error
        self.calls = 0

    async def search(self, params: SearchParams) -> list[SerpResult]:
        self.calls += 1
        if self.error:
            raise self.error
        return [SerpResult(serp_position=1, query="q", searchengine="yandex", url="u", domain=self.name, title="")]


def Stub(name: str, error: Exception | None = None) -> _Stub:
    return type(f"Stub_{name}", (_Stub,), {"name": name})(error)


def test_first_provider_wins() -> None:
    first, second = Stub("a"), Stub("b")
    results = SerpClient(providers=[first, second]).search_sync(SearchParams(query="q"))
    assert results[0].domain == "a"
    assert second.calls == 0


def test_falls_back_to_next_provider() -> None:
    first, second = Stub("a", ProviderError("a", "down")), Stub("b")
    results = SerpClient(providers=[first, second]).search_sync(SearchParams(query="q"))
    assert results[0].domain == "b"


def test_single_failure_is_reraised_as_is() -> None:
    with pytest.raises(AuthRequired):
        SerpClient(Stub("a", AuthRequired("a"))).search_sync(SearchParams(query="q"))


def test_all_failed_is_one_provider_error() -> None:
    chain = [Stub("a", AuthRequired("a")), Stub("b", ProviderError("b", "down"))]
    with pytest.raises(ProviderError, match="a\\+b") as excinfo:
        SerpClient(providers=chain).search_sync(SearchParams(query="q"))
    assert "down" in excinfo.value.message


def test_unknown_provider_name_fails_upfront() -> None:
    with pytest.raises(InvalidArguments):
        SerpClient(providers=["nope"])


def test_default_chain_without_credentials_is_auth_required() -> None:
    with pytest.raises(AuthRequired):
        SerpClient().search_sync(SearchParams(query="q"))
