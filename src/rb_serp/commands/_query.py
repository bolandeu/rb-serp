"""Общие параметры запроса для search / snippets / ads / related / summary."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

import typer

from .. import output
from ..core import services
from ..core.models import PageFetch
from ..providers import providers_help


class Engine(StrEnum):
    yandex = "yandex"
    google = "google"
    both = "both"


QueriesArg = typer.Argument(..., help="Один или несколько поисковых запросов.")
EngineOpt = typer.Option(Engine.yandex, "--engine", "-e", help="Поисковая система.")
TopOpt = typer.Option(10, "--top", "-n", min=10, max=100, help="Количество результатов (кратно 10).")
YandexLrOpt = typer.Option(
    None,
    "--yandex-lr",
    help="Код региона Яндекса (lr), по умолчанию 213=Москва; 2=СПб, 35=Краснодар. "
    "Все коды: https://yandex.ru/dev/xml/doc/ru/reference/regions",
)
GoogleLocOpt = typer.Option(
    None,
    "--google-loc",
    help="Criteria ID локации Google, по умолчанию 1011969=Москва; 1012153=СПб. "
    "Все коды: https://xmlriver.com/files/geo.csv",
)
ProviderOpt = typer.Option(
    None,
    "--provider",
    "-p",
    help="Очередь провайдеров через запятую: первый основной, остальные фолбэки. "
    f"Доступны: {providers_help()}. "
    "По умолчанию — `providers` из конфига проекта, иначе xmlriver.",
)
CacheOpt = typer.Option(
    False,
    "--cache",
    help="Взять ответ из журнала, если такой запрос (поисковик, регион, текст, страница) уже был — "
    "бесплатно. Только полные ответы провайдеров из --provider, в порядке очереди: ответ XMLStock "
    "не выдаётся за ответ XML River. Без флага — всегда живой запрос. Ответы пишутся в журнал всегда.",
)
MaxAgeOpt = typer.Option(
    None, "--max-age", help="С --cache: брать из журнала, только если запись не старше срока: 30m, 12h, 7d, 2w."
)
ForceOpt = typer.Option(False, "--force", help="Всегда живой запрос, даже с --cache; новый ответ станет актуальным.")


def collect(
    ctx: typer.Context,
    queries: list[str],
    *,
    engine: Engine,
    top: int,
    yandex_lr: int | None,
    google_loc: str | None,
    provider: str | None,
    cache: bool,
    max_age: str | None,
    force: bool,
    first_page_only: bool = False,
    on_query_done=None,
) -> list[PageFetch]:
    """Разобрать общие параметры и получить страницы выдачи."""
    providers = services.resolve_providers(provider, Path(ctx.obj.get("project") or Path.cwd()))
    output.vecho(ctx, f"Провайдеры: {', '.join(providers)}")
    return services.collect(
        queries,
        engine=engine.value,
        top=top,
        providers=providers,
        yandex_lr=yandex_lr,
        google_loc=google_loc,
        use_cache=cache and not force,
        max_age=services.parse_age(max_age) if max_age else None,
        first_page_only=first_page_only,
        on_query_done=on_query_done,
    )


def billing(ctx: typer.Context, pages: list[PageFetch]) -> dict:
    """Поля стоимости в --json: sources[] с billable_requests и cost*, итоги по всем страницам.

    У оплаченного провайдера нет цены — предупреждение в stderr, cost null (не ошибка).
    """
    live = services.billed_providers(pages)
    prices = services.provider_prices(live, Path(ctx.obj.get("project") or Path.cwd()))
    for name in (name for name in live if prices[name] is None):
        typer.echo(
            f"Предупреждение: цена запроса {name} не задана — cost null. "
            f"Задайте {services.price_env_var(name)} или [prices] {name} в .serp/config.toml.",
            err=True,
        )
    sources, cost_total = services.priced_sources(pages, prices)
    return {
        "sources": sources,
        "billable_requests_total": sum(source["billable_requests"] for source in sources),
        "cost_total": cost_total,
    }


def human_mode(ctx: typer.Context) -> bool:
    return not ctx.obj.get("json") and not ctx.obj.get("quiet")
