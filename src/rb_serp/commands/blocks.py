"""Блоки ответа кроме позиций: сниппеты, реклама, похожие запросы, сводка."""

from __future__ import annotations

import typer
from rich.table import Table

from .. import output
from ..core import services
from . import _query as q


def _table(rows: list[dict], columns: dict[str, str]) -> None:
    """Таблица в stdout: `columns` — поле → заголовок."""
    if not rows:
        output.human.echo("Ничего нет.")
        return
    table = Table(show_header=True, header_style="bold cyan")
    for title in columns.values():
        table.add_column(title, overflow="fold")
    for row in rows:
        table.add_row(*(str(row.get(field) if row.get(field) is not None else "") for field in columns))
    output.human.console.print(table)


def _emit(ctx: typer.Context, rows: list[dict], pages, columns: dict[str, str]) -> None:
    output.emit_success(
        ctx,
        {"count": len(rows), "results": rows, **q.billing(ctx, pages)},
        render_human=lambda _: _table(rows, columns),
    )


@output.guard
def snippets(
    ctx: typer.Context,
    queries: list[str] = q.QueriesArg,
    engine: q.Engine = q.EngineOpt,
    top: int = q.TopOpt,
    yandex_lr: int | None = q.YandexLrOpt,
    google_loc: str | None = q.GoogleLocOpt,
    provider: str | None = q.ProviderOpt,
    cache: bool = q.CacheOpt,
    max_age: str | None = q.MaxAgeOpt,
    force: bool = q.ForceOpt,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Выдача со всеми полями: сниппет, подсветки, хлебные крошки, рейтинг, имя сайта, дата.

    Те же позиции, что у search, плюс всё, что провайдер отдал о результате.
    Какие поля заполнены, зависит от провайдера и поисковика: breadcrumbs —
    везде, кроме XML River/Яндекс; stars, pubdate, cachelink — XML River/Google;
    site_name — XMLStock/Google.

    --json: {"ok": true, "count": N, "results": [{serp_position, query,
    searchengine, url, domain, title, snippet, breadcrumbs, stars, site_name,
    pubdate, cachelink, contenttype, highlights}, ...], "sources": [...],
    "billable_requests_total": N, "cost_total": X}. sources[] и стоимость —
    как у search (см. serp search --help): с --cache ответ из журнала
    бесплатный — billable_requests 0, cost 0, cost_source "cache".

    \b
    Example: serp snippets "купить диван" --cache
             serp snippets "купить диван" -e google --top 20 --json
    """
    pages = q.collect(
        ctx, queries, engine=engine, top=top, yandex_lr=yandex_lr, google_loc=google_loc,
        provider=provider, cache=cache, max_age=max_age, force=force,
    )  # fmt: skip
    _emit(
        ctx,
        services.snippets(pages, top),
        pages,
        {"serp_position": "#", "searchengine": "Поисковик", "domain": "Домен", "title": "Title", "snippet": "Сниппет"},
    )


@output.guard
def ads(
    ctx: typer.Context,
    queries: list[str] = q.QueriesArg,
    engine: q.Engine = q.EngineOpt,
    yandex_lr: int | None = q.YandexLrOpt,
    google_loc: str | None = q.GoogleLocOpt,
    provider: str | None = q.ProviderOpt,
    cache: bool = q.CacheOpt,
    max_age: str | None = q.MaxAgeOpt,
    force: bool = q.ForceOpt,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Реклама на первой странице выдачи: над выдачей (top) и под ней (bottom).

    На практике рекламу отдаёт только XMLStock для Яндекса (-p xmlstock):
    XML River рекламу Яндекса не запрашивает — с ней он урезает выдачу, а для
    Google оба сервиса рекламу обычно не возвращают. Нет рекламы — пустой
    список, не ошибка.

    --json: {"ok": true, "count": N, "results": [{query, searchengine,
    placement, position, url, domain, title, snippet, price}, ...],
    "sources": [...],
    "billable_requests_total": N, "cost_total": X}. sources[] и стоимость —
    как у search (см. serp search --help): с --cache ответ из журнала
    бесплатный — billable_requests 0, cost 0, cost_source "cache".

    \b
    Example: serp ads "купить диван" -p xmlstock
             serp ads "купить диван" -p xmlstock --cache --json
    """
    pages = q.collect(
        ctx, queries, engine=engine, top=10, yandex_lr=yandex_lr, google_loc=google_loc,
        provider=provider, cache=cache, max_age=max_age, force=force, first_page_only=True,
    )  # fmt: skip
    _emit(
        ctx,
        services.ads(pages),
        pages,
        {"searchengine": "Поисковик", "placement": "Где", "position": "#", "domain": "Домен", "title": "Title"},
    )


@output.guard
def related(
    ctx: typer.Context,
    queries: list[str] = q.QueriesArg,
    engine: q.Engine = q.EngineOpt,
    yandex_lr: int | None = q.YandexLrOpt,
    google_loc: str | None = q.GoogleLocOpt,
    provider: str | None = q.ProviderOpt,
    cache: bool = q.CacheOpt,
    max_age: str | None = q.MaxAgeOpt,
    force: bool = q.ForceOpt,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Похожие запросы (kind=search) и похожие вопросы (kind=question).

    На практике это Google: похожие запросы — XMLStock, похожие вопросы —
    оба сервиса (у XML River с ответом и ссылкой, когда они есть). Для Яндекса
    обычно пусто. Нет блока — пустой список, не ошибка.

    --json: {"ok": true, "count": N, "results": [{query, searchengine, kind,
    text, snippet, url}, ...], "sources": [...],
    "billable_requests_total": N, "cost_total": X}. sources[] и стоимость —
    как у search (см. serp search --help): с --cache ответ из журнала
    бесплатный — billable_requests 0, cost 0, cost_source "cache".

    \b
    Example: serp related "купить диван" -e google -p xmlstock
             serp related "купить диван" -e google --cache --json
    """
    pages = q.collect(
        ctx, queries, engine=engine, top=10, yandex_lr=yandex_lr, google_loc=google_loc,
        provider=provider, cache=cache, max_age=max_age, force=force, first_page_only=True,
    )  # fmt: skip
    _emit(ctx, services.related(pages), pages, {"searchengine": "Поисковик", "kind": "Тип", "text": "Текст"})


@output.guard
def summary(
    ctx: typer.Context,
    queries: list[str] = q.QueriesArg,
    engine: q.Engine = q.EngineOpt,
    yandex_lr: int | None = q.YandexLrOpt,
    google_loc: str | None = q.GoogleLocOpt,
    provider: str | None = q.ProviderOpt,
    cache: bool = q.CacheOpt,
    max_age: str | None = q.MaxAgeOpt,
    force: bool = q.ForceOpt,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Сводка по первой странице: сколько нашлось, есть ли ИИ-ответ, число блоков.

    found — сколько результатов у поисковика (Google через оба сервиса
    отдаёт условное 100); ai_present — показан ли ИИ-ответ (только Google, сам
    текст платный и не запрашивается); knowledge_graph — карточка знаний
    Google (XML River), только непустые поля.

    --json: {"ok": true, "count": N, "results": [{query, searchengine, found,
    ai_present, organic, ads, related, knowledge_graph}, ...], "sources": [...],
    "billable_requests_total": N, "cost_total": X}. sources[] и стоимость —
    как у search (см. serp search --help): с --cache ответ из журнала
    бесплатный — billable_requests 0, cost 0, cost_source "cache".

    \b
    Example: serp summary "купить диван" -e both --cache
    """
    pages = q.collect(
        ctx, queries, engine=engine, top=10, yandex_lr=yandex_lr, google_loc=google_loc,
        provider=provider, cache=cache, max_age=max_age, force=force, first_page_only=True,
    )  # fmt: skip
    _emit(
        ctx,
        services.summary(pages),
        pages,
        {
            "query": "Запрос",
            "searchengine": "Поисковик",
            "found": "Нашлось",
            "ai_present": "ИИ-ответ",
            "ads": "Реклама",
            "related": "Похожие",
        },
    )
