from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from ..providers import SerpProvider, provider_class
from .errors import AuthRequired, ProviderError
from .models import DEFAULT_PROVIDERS, SearchParams, SerpResult

log = logging.getLogger(__name__)


class SerpClient:
    """Основной клиент для получения поисковой выдачи.

    `providers` — очередь: имена из `providers.PROVIDERS` или готовые
    экземпляры. Первый — основной, остальные — фолбэки: следующий
    пробуется, только если предыдущий упал с `ProviderError`/`AuthRequired`.
    Отказали все — поднимается ошибка (одна — как есть, несколько —
    сводный `ProviderError`).
    """

    def __init__(
        self,
        provider: SerpProvider | None = None,
        *,
        providers: Sequence[str | SerpProvider] | None = None,
    ) -> None:
        if provider is not None and providers is not None:
            raise ValueError("Передайте либо provider, либо providers.")
        self._chain: list[str | SerpProvider] = [provider] if provider else list(providers or DEFAULT_PROVIDERS)
        if not self._chain:
            raise ValueError("Очередь провайдеров пуста.")
        for item in self._chain:
            if isinstance(item, str):
                provider_class(item)  # неизвестное имя — ошибка сразу, а не после сбоя основного

    async def search(self, params: SearchParams) -> list[SerpResult]:
        """Асинхронный поиск с переходом по очереди провайдеров."""
        errors: list[ProviderError | AuthRequired] = []
        for index, item in enumerate(self._chain):
            name = item if isinstance(item, str) else item.name
            try:
                provider = provider_class(item)() if isinstance(item, str) else item
                return await provider.search(params)
            except (ProviderError, AuthRequired) as exc:
                errors.append(exc)
                has_next = index + 1 < len(self._chain)
                log.warning("Провайдер %s: %s%s", name, exc.message, " — пробую следующий" if has_next else "")
        if len(errors) == 1:
            raise errors[0]
        raise ProviderError(
            "+".join(item if isinstance(item, str) else item.name for item in self._chain),
            "все провайдеры отказали: " + "; ".join(exc.message for exc in errors),
        )

    def search_sync(self, params: SearchParams) -> list[SerpResult]:
        """Синхронная обёртка над async search."""
        return asyncio.run(self.search(params))
