from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import ClassVar

from ..core.models import PageRequest
from ..credentials import manager as credentials


class SerpProvider(ABC):
    """Абстрактный интерфейс провайдера поисковой выдачи.

    Провайдер умеет одно: получить одну страницу выдачи и вернуть сырой ответ
    в формате Яндекс.XML (`<yandexsearch>`) — со всеми бесплатными блоками, что
    сервис отдаёт. Разбор (`core.serp_xml`), кэш, фолбэк по очереди и сборку
    страниц делает `SerpClient`.

    Подкласс задаёт `name` (ключ в реестре `providers.PROVIDERS` и имя
    credential в keychain), `summary` (строка для --help) и
    `credential_fields`. Сбои внешнего сервиса поднимать как `ProviderError` /
    `AuthRequired`: на них `SerpClient` переключается на следующий провайдер.
    """

    name: ClassVar[str]
    # Одна строка для --help: сервис и какие движки умеет.
    summary: ClassVar[str] = ""
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
    async def fetch_page(self, request: PageRequest) -> str:
        """Сырой XML одной страницы. Пустая выдача — валидный ответ, не ошибка."""
        ...

    async def aclose(self) -> None:  # noqa: B027 — по умолчанию закрывать нечего
        """Освободить сетевые ресурсы; клиент вызывает в конце каждого поиска."""
