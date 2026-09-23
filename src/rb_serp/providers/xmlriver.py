from __future__ import annotations

import asyncio
import logging
import xml.etree.ElementTree as ET

import httpx

from ..core.errors import AuthRequired, ProviderError
from ..core.models import PageRequest
from ..core.serp_xml import parse_page, response_error
from .base import SerpProvider

log = logging.getLogger(__name__)

_BASE_URL_YANDEX = "https://xmlriver.com/search_yandex/xml"
_BASE_URL_GOOGLE = "https://xmlriver.com/search/xml"
_MAX_ATTEMPTS = 10
_RETRY_DELAY = 1.0
_RETRY_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})

# Коды <error code="..."> из https://xmlriver.com/api/api-errors/
_NO_RESULTS = 15
_AUTH_ERRORS = frozenset({31, 42})
_RETRY_ERRORS = frozenset({20, 21, 22, 23, 24, 101, 110, 111, 202, 203, 500})

# Google: все бесплатные доп. блоки (платный только `ai` — текст ИИ-ответа, его не
# берём), https://xmlriver.com/apidoc/api-about/. Для Яндекса доп. блоки
# (`additional=y_topads,...`) не запрашиваем: с ними XML River отдаёт 1–8 позиций
# вместо 10+ (проверено 2026-09-23); рекламу Яндекса отдаёт XMLStock.
_GOOGLE_ADDITIONAL = (
    "topads,bottomads,faqsnippet,localresultsplace,rq,rs,es,cachelink,google_first_position,sitelinks,"
    "oneline_sitelinks,knowledge_graph,g_inlineshopping,g_podcasts,app,g_news,g_videos,g_discuss"
)


class XmlRiverProvider(SerpProvider):
    """Провайдер поисковой выдачи через XML River API."""

    name = "xmlriver"
    summary = "XML River — yandex и google"
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
        self._client: httpx.AsyncClient | None = None

    def _base_url(self, engine: str) -> str:
        """Адрес API для движка; подкласс с тем же форматом ответа меняет только его."""
        return _BASE_URL_YANDEX if engine == "yandex" else _BASE_URL_GOOGLE

    def _build_params(self, request: PageRequest) -> dict:
        """Параметры запроса: регион, страница и (для Google) все бесплатные доп. блоки."""
        base: dict = {"user": self._user, "key": self._token, "query": request.query}
        if request.engine == "yandex":
            base.update(
                {
                    "lr": request.yandex_lr,
                    "device": "mobile",
                    "lang": "ru",
                    "groupby": "10",
                    "page": request.page,
                }
            )
        else:  # google: страницы с 1; `start` сервис понимает неверно (выдача не по запросу)
            base.update(
                {
                    "lr": request.google_country,
                    "country": "2643",
                    "loc": request.google_loc,
                    "page": request.page + 1,
                    "ads": 1,
                    "additional": _GOOGLE_ADDITIONAL,
                }
            )
        return base

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=60.0, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        # AsyncClient привязан к event loop, а search_sync каждый раз запускает новый.
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_page(self, request: PageRequest) -> str:
        """Одна страница выдачи. Временные сбои повторяются, остальное — ProviderError/AuthRequired."""
        where = f"[{request.engine}] '{request.query}' стр.{request.page}"
        last_error = "нет ответа"

        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = await self._http().get(self._base_url(request.engine), params=self._build_params(request))
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
                        error = response_error(response.text)
                        has_results = error is None and bool(parse_page(response.text).organic)
                    except ET.ParseError as exc:
                        error, has_results = None, False
                        last_error = f"некорректный XML ({exc})"
                    else:
                        last_error = f"ошибка {error[0]}: {error[1]}" if error else "пустой ответ"
                    if has_results:
                        log.info("%s — ответ получен (попытка %d)", where, attempt)
                        return response.text
                    if error is not None:
                        code = error[0]
                        if code == _NO_RESULTS:
                            return response.text
                        if code in _AUTH_ERRORS:
                            raise AuthRequired(self.name)
                        if code not in _RETRY_ERRORS:
                            raise ProviderError(self.name, f"{where}: {last_error}")

            log.warning("%s — %s (попытка %d/%d)", where, last_error, attempt, _MAX_ATTEMPTS)
            if attempt < _MAX_ATTEMPTS:
                await asyncio.sleep(self._retry_delay)

        raise ProviderError(self.name, f"{where}: {last_error} после {_MAX_ATTEMPTS} попыток")
