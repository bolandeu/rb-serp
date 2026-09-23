from __future__ import annotations

from ..core.errors import ProviderError
from .xmlriver import XmlRiverProvider

_BASE_URL_YANDEX = "https://xmlstock.com/yandexlive/xml/"


class XmlStockProvider(XmlRiverProvider):
    """Провайдер выдачи Яндекса через XMLStock («Яндекс Live»).

    Ответ — тот же формат Яндекс.XML, что у XML River (`<group>/<doc>`,
    `<error code>` с теми же кодами: 15 — пусто, 31/42 — доступ), и те же
    параметры запроса (`query`, `lr`, `page`, `groupby`, `device`, `lang`),
    поэтому отличается только адрес. `token` — это `key` из кабинета XMLStock.
    Google не поддерживается: `ProviderError`, и клиент уходит к следующему
    провайдеру очереди.
    """

    name = "xmlstock"
    summary = "XMLStock — только yandex"
    credential_fields = ("user", "token")
    legacy_env = {}  # noqa: RUF012 — ClassVar в базе

    def _base_url(self, engine: str) -> str:
        if engine != "yandex":
            raise ProviderError(self.name, f"движок {engine} не поддерживается, только yandex")
        return _BASE_URL_YANDEX
