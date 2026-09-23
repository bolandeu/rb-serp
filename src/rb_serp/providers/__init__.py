"""SERP-провайдеры: один модуль на внешний сервис, все регистрируются в `PROVIDERS`.

Новый провайдер: подкласс `SerpProvider` в своём модуле + строка в
`PROVIDERS`. Дальше он доступен по имени в `serp search --provider`,
`serp auth login --provider` и в `providers` конфига проекта.
"""

from __future__ import annotations

from ..core.errors import InvalidArguments
from .base import SerpProvider
from .xmlriver import XmlRiverProvider
from .xmlstock import XmlStockProvider

PROVIDERS: dict[str, type[SerpProvider]] = {
    XmlRiverProvider.name: XmlRiverProvider,
    XmlStockProvider.name: XmlStockProvider,
}


def provider_class(name: str) -> type[SerpProvider]:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise InvalidArguments(f"Неизвестный провайдер '{name}'. Доступны: {', '.join(PROVIDERS)}.") from None


__all__ = ["PROVIDERS", "SerpProvider", "XmlRiverProvider", "XmlStockProvider", "provider_class"]
