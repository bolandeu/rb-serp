"""SERP-провайдеры: один модуль на внешний сервис, все регистрируются в `PROVIDERS`.

Новый провайдер: подкласс `SerpProvider` в своём модуле + строка в
`PROVIDERS`. Дальше он доступен по имени в `serp search --provider`,
`serp auth login --provider` и в `providers` конфига проекта.

Каждый провайдер реализует `health_check()` — на нём держится
`serp auth status --check` (см. `base.Provider`).
"""

from __future__ import annotations

from ..core.errors import InvalidArguments
from .base import Health, Provider, Quota, SerpProvider, all_providers
from .xmlriver import XmlRiverProvider
from .xmlstock import XmlStockProvider

PROVIDERS: dict[str, type[SerpProvider]] = {
    XmlRiverProvider.name: XmlRiverProvider,
    XmlStockProvider.name: XmlStockProvider,
}


def providers_help() -> str:
    """Список провайдеров для --help: `xmlriver (XML River — ...), ...`."""
    return "; ".join(f"{name} ({cls.summary})" if cls.summary else name for name, cls in PROVIDERS.items())


def provider_class(name: str) -> type[SerpProvider]:
    try:
        return PROVIDERS[name]
    except KeyError:
        raise InvalidArguments(f"Неизвестный провайдер '{name}'. Доступны: {', '.join(PROVIDERS)}.") from None


__all__ = [
    "PROVIDERS",
    "Health",
    "Provider",
    "Quota",
    "SerpProvider",
    "XmlRiverProvider",
    "XmlStockProvider",
    "all_providers",
    "provider_class",
    "providers_help",
]
