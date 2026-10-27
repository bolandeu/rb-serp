"""Позиции органики — одно правило для `serp search`, записи в serp_results и миграции 0002.

Позиция считается по страницам, которые взял один вызов поиска (по одной на номер,
для одного запроса и поисковика): ответ страницы p идёт после взятых страниц с
меньшим номером, без дублей URL. Так позицию получает и взятая страница, и ответ,
который вызов не взял (неполный, повтор), — как если бы он был страницей p.
Почему таблица, а не представление над XML: docs/adr/0001-serp-results-table.md.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from rb_keys import norm_domain, norm_query, norm_url

from .models import OrganicItem, PageFetch


def page_positions(used: Iterable[PageFetch], page: PageFetch) -> list[tuple[int, OrganicItem]]:
    """(позиция, результат) органики `page` — после взятых страниц `used` с меньшим номером, без дублей URL."""
    before = sorted((prior for prior in used if prior.page < page.page), key=lambda prior: prior.page)
    seen: set[str] = set()
    position = 0
    positions: list[tuple[int, OrganicItem]] = []
    for source in [*before, page]:
        for item in source.data.organic:
            if item.url in seen:
                continue
            seen.add(item.url)
            position += 1
            if source is page:
                positions.append((position, item))
    return positions


def result_rows(region: str, page: PageFetch, used: Iterable[PageFetch]) -> list[dict]:
    """Строки serp_results одного ответа журнала `page` (его `fetched_at` — связь с ответом)."""
    response = {
        "fetched_at": page.fetched_at, "provider": page.provider, "engine": page.engine, "region": region,
        "query": page.query, "query_key": norm_query(page.query), "page": page.page, "complete": page.complete,
    }  # fmt: skip
    return [
        {
            **response,
            "position": position,
            "url": item.url,
            "url_key": _key(norm_url, item.url),
            "domain": item.domain,
            "domain_key": _key(norm_domain, item.domain),
            "title": item.title,
        }
        for position, item in page_positions(used, page)
    ]


def _key(norm: Callable[[str], str], value: str) -> str | None:
    """Ключ rb_keys, или None — значение не URL и не домен (rb_keys бросает ValueError)."""
    try:
        return norm(value)
    except ValueError:
        return None
