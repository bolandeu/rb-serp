from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import exc as sa_exc

from ..providers import SerpProvider, provider_class
from .cache import ResponseCache
from .errors import AuthRequired, ProviderError
from .models import OrganicItem, PageFetch, PageRequest, SearchParams, SerpResult, default_providers
from .serp_xml import parse_page

log = logging.getLogger(__name__)

_MAX_CONCURRENT = 10
# Сколько раз просить у провайдера неполную страницу, прежде чем перейти к следующему.
_INCOMPLETE_ATTEMPTS = 2


def organic_items(pages: list[PageFetch], top: int) -> list[tuple[PageFetch, int, OrganicItem]]:
    """Органика по каждому (запрос, поисковик): страницы по порядку, без дублей URL, ровно `top` позиций."""
    rows: list[tuple[PageFetch, int, OrganicItem]] = []
    groups: dict[tuple[str, str], list[PageFetch]] = {}
    for page in pages:
        groups.setdefault((page.query, page.engine), []).append(page)
    for group in groups.values():
        seen: set[str] = set()
        position = 0
        for page in sorted(group, key=lambda p: p.page):
            for item in page.data.organic:
                if item.url in seen or position >= top:
                    continue
                seen.add(item.url)
                position += 1
                rows.append((page, position, item))
    return rows


def organic_results(pages: list[PageFetch], top: int) -> list[SerpResult]:
    """Позиции органики (как `SerpClient.search`) из уже полученных страниц."""
    return [
        SerpResult(
            serp_position=position,
            query=page.query,
            searchengine=page.engine,
            url=item.url,
            domain=item.domain,
            title=item.title,
        )
        for page, position, item in organic_items(pages, top)
    ]


class SerpClient:
    """Основной клиент для получения поисковой выдачи.

    `providers` — очередь: имена из `providers.PROVIDERS` или готовые
    экземпляры. Первый — основной, остальные — фолбэки: для каждой страницы
    следующий пробуется, только если предыдущий упал с `ProviderError` /
    `AuthRequired`. Отказали все — ошибка (одна — как есть, несколько —
    сводный `ProviderError`).

    Неполная страница (меньше `MIN_FULL_PAGE` результатов при большом
    `found`) запрашивается повторно, затем у следующего провайдера; если полной
    так и нет — отдаётся лучшая с `complete=False`.

    `cache` — журнал всех ответов (`ResponseCache`): каждый ответ провайдера
    пишется туда всегда; `False` — не писать. Читать из него — только по
    `use_cache=True` в `fetch`/`search`, и только полные ответы провайдеров из
    очереди — в её порядке.
    """

    def __init__(
        self,
        provider: SerpProvider | None = None,
        *,
        providers: Sequence[str | SerpProvider] | None = None,
        cache: ResponseCache | bool = True,
    ) -> None:
        if provider is not None and providers is not None:
            raise ValueError("Передайте либо provider, либо providers.")
        self._chain: list[str | SerpProvider] = [provider] if provider else list(providers or default_providers())
        if not self._chain:
            raise ValueError("Очередь провайдеров пуста.")
        for item in self._chain:
            if isinstance(item, str):
                provider_class(item)  # неизвестное имя — ошибка сразу, а не после сбоя основного
        self.cache: ResponseCache | None = ResponseCache() if cache is True else (cache or None)

    async def fetch(
        self,
        params: SearchParams,
        *,
        pages: int | None = None,
        use_cache: bool = False,
        max_age: timedelta | None = None,
    ) -> list[PageFetch]:
        """Все страницы по всем запросам и поисковикам; `pages` — сколько (по умолчанию под `top`)."""
        self._open_journal()
        requests = [
            PageRequest(
                query=query,
                engine=engine,
                page=page,
                yandex_lr=params.yandex_lr,
                google_country=params.google_country,
                google_loc=params.google_loc,
            )
            for query in params.get_queries()
            for engine in params.get_engines()
            for page in range(params.num_pages if pages is None else pages)
        ]
        # Семафор и экземпляры провайдеров — на один вызов: они привязаны к
        # event loop, а search_sync каждый раз запускает новый.
        semaphore = asyncio.Semaphore(_MAX_CONCURRENT)
        instances: dict[int, SerpProvider | AuthRequired] = {}
        try:
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._fetch_one(request, instances, semaphore, use_cache, max_age))
                    for request in requests
                ]
        except ExceptionGroup as eg:
            raise eg.exceptions[0] from None
        finally:
            for instance in instances.values():
                if isinstance(instance, SerpProvider):
                    await instance.aclose()
        return [task.result() for task in tasks]

    async def search(
        self, params: SearchParams, *, use_cache: bool = False, max_age: timedelta | None = None
    ) -> list[SerpResult]:
        """Органическая выдача: позиции с 1, ровно `top` на запрос и поисковик."""
        return organic_results(await self.fetch(params, use_cache=use_cache, max_age=max_age), params.top)

    def search_sync(self, params: SearchParams, **kwargs) -> list[SerpResult]:
        """Синхронная обёртка над async search."""
        return asyncio.run(self.search(params, **kwargs))

    def fetch_sync(self, params: SearchParams, **kwargs) -> list[PageFetch]:
        """Синхронная обёртка над async fetch."""
        return asyncio.run(self.fetch(params, **kwargs))

    def _provider(self, index: int, instances: dict[int, SerpProvider | AuthRequired]) -> SerpProvider:
        """Экземпляр провайдера из очереди — создаётся один раз за вызов fetch."""
        if index not in instances:
            item = self._chain[index]
            try:
                instances[index] = provider_class(item)() if isinstance(item, str) else item
            except AuthRequired as exc:  # нет доступов — помним, чтобы не проверять на каждой странице
                instances[index] = exc
        instance = instances[index]
        if isinstance(instance, AuthRequired):
            raise instance
        return instance

    async def _fetch_one(
        self,
        request: PageRequest,
        instances: dict[int, SerpProvider | AuthRequired],
        semaphore: asyncio.Semaphore,
        use_cache: bool,
        max_age: timedelta | None,
    ) -> PageFetch:
        meta = {"query": request.query, "engine": request.engine, "page": request.page}
        if use_cache and self.cache is not None:
            # Из журнала — только ответ провайдера из очереди и в её порядке: у
            # провайдеров разные доп. блоки, ответ одного не подменяет другой.
            for name in self._names():
                hit = self.cache.latest(
                    provider=name,
                    engine=request.engine,
                    region=request.region,
                    query=request.query,
                    page=request.page,
                    max_age=max_age,
                )
                if hit is not None:
                    return PageFetch(
                        **meta,
                        provider=name,
                        fetched_at=hit.fetched_at,
                        from_cache=True,
                        billable={},
                        data=parse_page(hit.xml),
                    )

        errors: list[ProviderError | AuthRequired] = []
        best: PageFetch | None = None  # самая полная из неполных страниц
        # Платные ответы: каждый успешный (и неполный, и «нет результатов»), по провайдерам.
        billable: dict[str, int] = {}
        for index, name in enumerate(self._names()):
            has_next = index + 1 < len(self._chain)
            for attempt in range(1, _INCOMPLETE_ATTEMPTS + 1):
                try:
                    provider = self._provider(index, instances)
                    async with semaphore:
                        xml = await provider.fetch_page(request)
                except (ProviderError, AuthRequired) as exc:
                    errors.append(exc)
                    log.warning("Провайдер %s: %s%s", name, exc.message, " — пробую следующий" if has_next else "")
                    break
                billable[name] = billable.get(name, 0) + 1
                data = parse_page(xml)
                page = PageFetch(
                    **meta,
                    provider=name,
                    fetched_at=self._save(name, request, xml, data.complete),
                    from_cache=False,
                    complete=data.complete,
                    billable=dict(billable),
                    data=data,
                )
                if data.complete:
                    return page
                log.warning(
                    "Провайдер %s: [%s] '%s' стр.%d — неполная страница, %d результатов при found=%s (попытка %d/%d)",
                    name, request.engine, request.query, request.page, len(data.organic), data.found,
                    attempt, _INCOMPLETE_ATTEMPTS,
                )  # fmt: skip
                if best is None or len(data.organic) > len(best.data.organic):
                    best = page

        if best is not None:
            log.warning("Полной страницы не получено — отдаю лучшую (complete=false, в --cache не попадёт)")
            return best.model_copy(update={"billable": billable})  # все платные ответы, не только до лучшего
        if len(errors) == 1:
            raise errors[0]
        if all(isinstance(exc, AuthRequired) for exc in errors):
            # Доступов нет ни у кого — подсказать логин в обязательный провайдер, а не «все отказали».
            required = [exc for exc, item in zip(errors, self._chain, strict=True) if self._required(item)]
            raise (required or errors)[0]
        raise ProviderError(
            "+".join(self._names()),
            "все провайдеры отказали: " + "; ".join(exc.message for exc in errors),
        )

    @staticmethod
    def _required(item: str | SerpProvider) -> bool:
        return (provider_class(item) if isinstance(item, str) else item).required

    def _names(self) -> list[str]:
        return [item if isinstance(item, str) else item.name for item in self._chain]

    def _open_journal(self) -> None:
        """Открыть журнал до первого платного запроса.

        Общая база недоступна или не мигрирована (`CliError`: DATABASE_UNAVAILABLE,
        DATABASE_NOT_MIGRATED, CLI_TOO_OLD) — ошибка сразу, а не оплаченный ответ,
        который некуда записать. Сбой локального файла — только предупреждение,
        как и при записи.
        """
        if self.cache is None:
            return
        try:
            self.cache.engine  # noqa: B018 — открывает базу
        except (sa_exc.SQLAlchemyError, OSError) as exc:
            log.warning("Журнал %s недоступен: %s", self.cache.path, exc)

    def _save(self, provider: str, request: PageRequest, xml: str, complete: bool) -> datetime:
        """Записать ответ в журнал; сбой диска не должен ломать уже оплаченный поиск."""
        if self.cache is not None:
            try:
                return self.cache.put(
                    provider=provider,
                    engine=request.engine,
                    region=request.region,
                    query=request.query,
                    page=request.page,
                    xml=xml,
                    complete=complete,
                )
            except (sa_exc.SQLAlchemyError, OSError) as exc:
                log.warning("Не удалось записать ответ в журнал %s: %s", self.cache.path, exc)
        return datetime.now(UTC)
