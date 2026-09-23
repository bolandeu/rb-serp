from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET

import httpx

from ..core.errors import AuthRequired, ProviderError
from ..core.models import SearchParams, SerpResult, extract_domain
from .base import SerpProvider

log = logging.getLogger(__name__)

_BASE_URL_YANDEX = "https://xmlriver.com/search_yandex/xml"
_BASE_URL_GOOGLE = "https://xmlriver.com/search/xml"
_MAX_CONCURRENT = 10
_MAX_ATTEMPTS = 10
_RETRY_DELAY = 1.0
_RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# Коды <error code="..."> из https://xmlriver.com/api/api-errors/
_NO_RESULTS = 15
_AUTH_ERRORS = frozenset({31, 42})
_RETRY_ERRORS = frozenset({20, 21, 22, 23, 24, 101, 110, 111, 202, 203, 500})


def parse_xml(xml_text: str) -> tuple[list[tuple[str, str]], int | None, str]:
    """Разбирает ответ: ([(url, title)], код ошибки или None, текст ошибки)."""
    root = ET.fromstring(xml_text)  # noqa: S314
    error = root.find(".//error")
    if error is not None:
        code = error.get("code")
        return [], int(code) if code and code.isdigit() else None, (error.text or "").strip()

    docs = [group.find("doc") for group in root.findall(".//group")]
    if not any(doc is not None for doc in docs):
        docs = root.findall(".//doc")
    results: list[tuple[str, str]] = []
    for doc in docs:
        if doc is None:
            continue
        url = doc.findtext("url")
        if url:
            # Яндекс подсвечивает слова запроса <hlword> внутри title: findtext
            # вернул бы только текст до первой подсветки.
            title = doc.find("title")
            results.append((url, "".join(title.itertext()) if title is not None else ""))
    return results, None, ""


class XmlRiverProvider(SerpProvider):
    """Провайдер поисковой выдачи через XML River API."""

    name = "xmlriver"
    credential_fields = ("user", "token")
    legacy_env = {"user": "XMLRIVER_USER", "token": "XMLRIVER_TOKEN"}  # noqa: RUF012 — ClassVar в базе

    def __init__(
        self,
        credential: dict[str, str] | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        retry_delay: float = _RETRY_DELAY,
    ) -> None:
        credential = credential or self.load_credential()
        if credential is None:
            raise AuthRequired(self.name)
        self._user = credential["user"]
        self._token = credential["token"]
        self._transport = transport  # тестовый шов для httpx.MockTransport
        self._retry_delay = retry_delay

    def _base_url(self, engine: str) -> str:
        """Адрес API для движка; подкласс с тем же форматом ответа меняет только его."""
        return _BASE_URL_YANDEX if engine == "yandex" else _BASE_URL_GOOGLE

    def _build_params(self, query: str, engine: str, page: int, params: SearchParams) -> dict:
        """Формирует параметры запроса для выбранной поисковой системы."""
        base: dict = {"user": self._user, "key": self._token}
        if engine == "yandex":
            base.update(
                {
                    "query": query,
                    "lr": params.yandex_lr,
                    "device": "mobile",
                    "lang": "ru",
                    "groupby": "10",
                    "page": page,
                }
            )
        else:  # google
            base.update(
                {
                    "query": query,
                    "lr": params.google_country,
                    "groupby": "10",
                    "country": "2643",
                    "loc": params.google_loc,
                    "start": page * 10,
                }
            )
        return base

    async def _fetch_page(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        query: str,
        engine: str,
        page: int,
        params: SearchParams,
    ) -> list[tuple[str, str]]:
        """Одна страница выдачи. Временные сбои повторяются, остальное — ProviderError/AuthRequired."""
        base_url = self._base_url(engine)
        request_params = self._build_params(query, engine, page, params)
        where = f"[{engine}] '{query}' стр.{page}"
        last_error = "нет ответа"

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            async with semaphore:
                try:
                    response = await client.get(base_url, params=request_params)
                except httpx.TransportError as exc:
                    last_error = type(exc).__name__
                else:
                    status = response.status_code
                    if status in (401, 403):
                        raise AuthRequired(self.name)
                    if response.is_error:
                        if status not in _RETRY_HTTP_STATUSES:
                            raise ProviderError(self.name, f"{where}: HTTP {status}", status=status)
                        last_error = f"HTTP {status}"
                    else:
                        try:
                            items, code, message = parse_xml(response.text)
                        except ET.ParseError as exc:
                            items, code, message = [], None, ""
                            last_error = f"некорректный XML ({exc})"
                        else:
                            last_error = f"ошибка {code}: {message}" if code is not None else "пустой ответ"
                        if items:
                            log.info("%s — получено %d результатов (попытка %d)", where, len(items), attempt)
                            return items
                        if code == _NO_RESULTS:
                            return []
                        if code in _AUTH_ERRORS:
                            raise AuthRequired(self.name)
                        if code is not None and code not in _RETRY_ERRORS:
                            raise ProviderError(self.name, f"{where}: {last_error}")

            log.warning("%s — %s (попытка %d/%d)", where, last_error, attempt, _MAX_ATTEMPTS)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(self._retry_delay)

        raise ProviderError(self.name, f"{where}: {last_error} после {_MAX_ATTEMPTS} попыток")

    async def _search_one(
        self,
        client: httpx.AsyncClient,
        semaphore: asyncio.Semaphore,
        query: str,
        engine: str,
        params: SearchParams,
    ) -> list[SerpResult]:
        """Получает результаты для одного запроса и одного движка."""
        pages = await asyncio.gather(
            *(self._fetch_page(client, semaphore, query, engine, page, params) for page in range(params.num_pages))
        )
        items = [item for page_items in pages for item in page_items]
        return [
            SerpResult(
                serp_position=position,
                query=query,
                searchengine=engine,
                url=url,
                domain=extract_domain(url),
                title=title,
            )
            for position, (url, title) in enumerate(items, start=1)
        ]

    async def search(self, params: SearchParams) -> list[SerpResult]:
        """Выполняет поиск по всем запросам и движкам параллельно; любой сбой прерывает весь поиск."""
        # Семафор создаётся на каждый вызов: он привязывается к event loop, а
        # search_sync запускает новый loop на каждый вызов.
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        async with httpx.AsyncClient(timeout=30.0, transport=self._transport) as client:
            try:
                async with asyncio.TaskGroup() as group:
                    tasks = [
                        group.create_task(self._search_one(client, semaphore, query, engine, params))
                        for query in params.get_queries()
                        for engine in params.get_engines()
                    ]
            except ExceptionGroup as eg:
                raise eg.exceptions[0] from None
        return [result for task in tasks for result in task.result()]
