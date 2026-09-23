from __future__ import annotations

from ..core.models import PageRequest
from .xmlriver import XmlRiverProvider

_BASE_URL_YANDEX = "https://xmlstock.com/yandexlive/xml/"
_BASE_URL_GOOGLE = "https://xmlstock.com/google/xml/"


class XmlStockProvider(XmlRiverProvider):
    """Провайдер выдачи через XMLStock: Яндекс («Яндекс Live») и Google.

    Ответ — тот же формат Яндекс.XML, что у XML River (`<group>/<doc>`,
    `<error code>` с теми же кодами: 15 — пусто, 31/42 — доступ), так что
    повторы и разбор общие; свои только адреса и параметры
    (https://xmlstock.com/?do=help-yalive-params, ?do=help-googlexml-params):
    у Google регион — Criteria ID в `lr`, страница — `page` с нуля.
    `token` — это `key` из кабинета XMLStock.
    """

    name = "xmlstock"
    summary = "XMLStock — yandex и google"
    credential_fields = ("user", "token")
    legacy_env = {}  # noqa: RUF012 — ClassVar в базе

    def _base_url(self, engine: str) -> str:
        return _BASE_URL_YANDEX if engine == "yandex" else _BASE_URL_GOOGLE

    def _build_params(self, request: PageRequest) -> dict:
        base: dict = {"user": self._user, "key": self._token, "query": request.query, "page": request.page}
        if request.engine == "yandex":
            base.update({"lr": request.yandex_lr, "device": "mobile", "lang": "ru"})
        else:
            base.update({"lr": request.google_loc, "hl": "ru"})
        base.update({"ads": 1, "related": 1})  # бесплатные доп. блоки: реклама и похожие запросы
        return base
