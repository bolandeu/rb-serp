from __future__ import annotations

from ..core.models import SearchParams
from .xmlriver import XmlRiverProvider

_BASE_URL_YANDEX = "https://xmlstock.com/yandexlive/xml/"
_BASE_URL_GOOGLE = "https://xmlstock.com/google/xml/"


class XmlStockProvider(XmlRiverProvider):
    """Провайдер выдачи через XMLStock: Яндекс («Яндекс Live») и Google.

    Ответ — тот же формат Яндекс.XML, что у XML River (`<group>/<doc>`,
    `<error code>` с теми же кодами: 15 — пусто, 31/42 — доступ). Параметры
    Яндекса совпадают с XML River, у Google свои (https://xmlstock.com/?do=help-googlexml-params):
    регион — Criteria ID в `lr` (у XML River это `loc`), страница — `page`
    с нуля. `token` — это `key` из кабинета XMLStock.
    """

    name = "xmlstock"
    summary = "XMLStock — yandex и google"
    credential_fields = ("user", "token")
    legacy_env = {}  # noqa: RUF012 — ClassVar в базе

    def _base_url(self, engine: str) -> str:
        return _BASE_URL_YANDEX if engine == "yandex" else _BASE_URL_GOOGLE

    def _build_params(self, query: str, engine: str, page: int, params: SearchParams) -> dict:
        if engine == "yandex":
            return super()._build_params(query, engine, page, params)
        return {
            "user": self._user,
            "key": self._token,
            "query": query,
            "lr": params.google_loc,
            "hl": "ru",
            "page": page,
        }
