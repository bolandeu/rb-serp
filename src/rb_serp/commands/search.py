from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path

import typer
from rich.table import Table

from .. import output
from ..core import services
from ..core.models import SerpResult
from . import _query as q


class Format(StrEnum):
    table = "table"
    csv = "csv"
    domains = "domains"
    domains_se = "domains-se"


def _print_table(results: list[SerpResult]) -> None:
    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("#", style="dim", width=4, justify="right")
    table.add_column("Поисковик", width=8)
    table.add_column("Домен", width=30)
    table.add_column("URL", overflow="fold")
    table.add_column("Title", overflow="fold")
    for r in results:
        table.add_row(str(r.serp_position), r.searchengine, r.domain, r.url, r.title)
    if results:
        output.human.err_console.print(f"\n[bold]Запрос:[/bold] {results[0].query}")
    output.human.err_console.print(table)


def _render(fmt: Format, results: list[SerpResult]) -> None:
    if fmt is Format.domains:
        for r in results:
            typer.echo(r.domain)
    elif fmt is Format.domains_se:
        for r in results:
            typer.echo(f"{r.searchengine}\t{r.domain}")
    elif fmt is Format.csv:
        services.write_csv(results, sys.stdout)
    # table уже выведен по мере готовности запросов


@output.guard
def search(
    ctx: typer.Context,
    queries: list[str] = q.QueriesArg,
    engine: q.Engine = q.EngineOpt,
    top: int = q.TopOpt,
    fmt: Format = typer.Option(Format.table, "--format", "-f", help="Формат вывода для человека."),
    output_path: Path | None = typer.Option(
        None, "--output", "-o", help="Дополнительно сохранить в файл: .csv → CSV, иначе JSON-массив."
    ),
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
    """Позиции в выдаче Yandex/Google: позиция, домен, URL, заголовок.

    Запросы выполняются по очереди; для каждой страницы провайдеры пробуются
    по порядку из --provider, пока один не ответит. Отказали все → ошибка
    PROVIDER_ERROR (exit 5) или AUTH_REQUIRED (exit 3). Пустая выдача без
    сбоя — не ошибка. Ровно --top позиций, дубли между страницами убраны.

    Ответ провайдера целиком (с рекламой, похожими запросами и т.д.) всегда
    пишется в журнал; другие блоки отдают команды snippets, ads, related,
    summary — с --cache они берут уже полученный ответ бесплатно.

    --json: {"ok": true, "count": N, "results": [{serp_position, query,
    searchengine, url, domain, title}, ...], "sources": [{query, engine,
    page, provider, fetched_at, from_cache, complete, cost, cost_currency},
    ...], "cost_total": X} (+ "saved_to" при --output). complete=false —
    провайдер отдал неполную страницу (повтор и следующий провайдер не
    помогли); из журнала такая не отдаётся.

    Стоимость: cost — рубли за страницу выдачи (cost_currency "RUB"),
    cost_total — сумма по всем страницам. Из журнала (from_cache=true) —
    0. Цена за запрос — по провайдеру: env SERP_<PROVIDER>_PRICE (например
    SERP_XMLRIVER_PRICE=0.25) перекрывает [prices] в .serp/config.toml
    (xmlriver = 0.25). Цена не задана — cost и cost_total null,
    предупреждение в stderr, не ошибка; не число — INVALID_ARGUMENTS.
    --format table печатает таблицу в stderr по мере готовности запросов;
    csv/domains/domains-se печатают данные в stdout.

    \b
    Example: serp search "python разработка"
             serp search "seo" "продвижение" --engine both --top 20
             serp search "такси" --yandex-lr 2 --format domains
             serp search "ремонт" -e google --google-loc 1012153
             serp search "запрос" -e both -p xmlstock,xmlriver   # xmlriver — фолбэк
             serp search "запрос" --cache --max-age 7d          # из журнала, если свежее недели
             serp search "запрос" --json --output results.csv
    """
    show_table = fmt is Format.table and q.human_mode(ctx)
    pages = q.collect(
        ctx,
        queries,
        engine=engine,
        top=top,
        yandex_lr=yandex_lr,
        google_loc=google_loc,
        provider=provider,
        cache=cache,
        max_age=max_age,
        force=force,
        on_query_done=(lambda query_pages: _print_table(services.organic(query_pages, top))) if show_table else None,
    )
    results = services.organic(pages, top)
    live = sorted({p.provider for p in pages if not p.from_cache})
    prices = services.provider_prices(live, Path(ctx.obj.get("project") or Path.cwd()))
    for name in (name for name in live if prices[name] is None):
        typer.echo(
            f"Предупреждение: цена запроса {name} не задана — cost null. "
            f"Задайте {services.price_env_var(name)} или [prices] {name} в .serp/config.toml.",
            err=True,
        )
    sources, cost_total = services.priced_sources(pages, prices)

    payload: dict = {
        "count": len(results),
        "results": [r.model_dump() for r in results],
        "sources": sources,
        "cost_total": cost_total,
    }
    if output_path is not None:
        services.save_results(results, output_path, as_csv=output_path.suffix.lower() == ".csv" or fmt is Format.csv)
        payload["saved_to"] = str(output_path)
        if q.human_mode(ctx):
            output.human.err_console.print(f"[green]Сохранено в {output_path}[/green]")

    output.emit_success(ctx, payload, render_human=lambda _: _render(fmt, results))
