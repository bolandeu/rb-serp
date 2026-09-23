from __future__ import annotations

import sys
from enum import StrEnum
from pathlib import Path

import typer
from rich.table import Table

from .. import output
from ..core import services
from ..core.models import SerpResult


class Engine(StrEnum):
    yandex = "yandex"
    google = "google"
    both = "both"


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
    queries: list[str] = typer.Argument(..., help="Один или несколько поисковых запросов."),
    engine: Engine = typer.Option(Engine.yandex, "--engine", "-e", help="Поисковая система."),
    top: int = typer.Option(10, "--top", "-n", min=10, max=100, help="Количество результатов (кратно 10)."),
    fmt: Format = typer.Option(Format.table, "--format", "-f", help="Формат вывода для человека."),
    output_path: Path | None = typer.Option(
        None, "--output", "-o", help="Дополнительно сохранить в файл: .csv → CSV, иначе JSON-массив."
    ),
    yandex_lr: int | None = typer.Option(
        None, "--yandex-lr", help="Код региона Яндекса (lr). Пример: 213=Москва, 2=СПб, 35=Краснодар."
    ),
    google_loc: str | None = typer.Option(
        None, "--google-loc", help="Criteria ID локации Google. Пример: 1011969=Москва, 1012153=СПб."
    ),
    provider: str | None = typer.Option(
        None,
        "--provider",
        "-p",
        help="Очередь провайдеров через запятую: первый основной, остальные фолбэки. "
        "По умолчанию — `providers` из конфига проекта, иначе xmlriver.",
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Поисковая выдача Yandex/Google по одному или нескольким запросам.

    Запросы выполняются по очереди; для каждого провайдеры пробуются по
    порядку из --provider, пока один не ответит. Отказали все → ошибка
    PROVIDER_ERROR (exit 5) или AUTH_REQUIRED (exit 3). Пустая выдача без
    сбоя — не ошибка.

    --json: {"ok": true, "count": N, "results": [{serp_position, query,
    searchengine, url, domain, title}, ...]} (+ "saved_to" при --output).
    --format table печатает таблицу в stderr по мере готовности запросов;
    csv/domains/domains-se печатают данные в stdout.

    \b
    Примеры:
      serp search "python разработка"
      serp search "seo" "продвижение" --engine both --top 20
      serp search "такси" --yandex-lr 2 --format domains
      serp search "запрос" --json --output results.csv
    """
    human_table = fmt is Format.table and not ctx.obj.get("json") and not ctx.obj.get("quiet")
    providers = services.resolve_providers(provider, Path(ctx.obj.get("project") or Path.cwd()))
    output.vecho(ctx, f"Провайдеры: {', '.join(providers)}")
    results = services.search(
        queries,
        engine=engine.value,
        top=top,
        providers=providers,
        yandex_lr=yandex_lr,
        google_loc=google_loc,
        on_query_done=_print_table if human_table else None,
    )

    payload: dict = {"count": len(results), "results": [r.model_dump() for r in results]}
    if output_path is not None:
        services.save_results(results, output_path, as_csv=output_path.suffix.lower() == ".csv" or fmt is Format.csv)
        payload["saved_to"] = str(output_path)
        if not ctx.obj.get("json") and not ctx.obj.get("quiet"):
            output.human.err_console.print(f"[green]Сохранено в {output_path}[/green]")

    output.emit_success(ctx, payload, render_human=lambda _: _render(fmt, results))
