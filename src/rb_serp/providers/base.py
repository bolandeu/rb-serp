from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar

from ..core.models import SearchParams, SerpResult
from ..credentials import manager as credentials


class SerpProvider(ABC):
    """Абстрактный интерфейс провайдера поисковой выдачи.

    Подкласс задаёт `name` (ключ в реестре `providers.PROVIDERS` и имя
    credential в keychain) и `credential_fields` — какие поля нужны для
    доступа. Сбои внешнего сервиса провайдер обязан поднимать как
    `ProviderError` / `AuthRequired`: на них `SerpClient` переключается на
    следующий провайдер из очереди.
    """

    name: ClassVar[str]
    credential_fields: ClassVar[tuple[str, ...]] = ()
    # Устаревшие имена env-переменных по полям, читаются, если поле не нашлось
    # ни в SERP_<PROVIDER>_<FIELD>, ни в keychain.
    legacy_env: ClassVar[dict[str, str]] = {}

    @classmethod
    def load_credential(cls) -> dict[str, str] | None:
        """Все `credential_fields` из env/keychain, или None, если хоть одного нет."""
        found = credentials.get_credential(cls.name, env_fields=list(cls.credential_fields)) or {}
        for field, env_name in cls.legacy_env.items():
            if not found.get(field) and os.environ.get(env_name):
                found[field] = os.environ[env_name]
        if all(found.get(field) for field in cls.credential_fields):
            return found
        return None

    @abstractmethod
    async def search(self, params: SearchParams) -> list[SerpResult]:
        """Выполняет поиск и возвращает результаты."""
        ...
