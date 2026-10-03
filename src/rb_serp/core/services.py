from __future__ import annotations

import csv
import json
import os
import re
import tomllib
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import TextIO

import tomli_w
from packaging.version import Version
from pydantic import ValidationError

from .client import SerpClient, organic_items, organic_results
from .errors import CliTooOld, InvalidArguments, ProjectNotInitialized
from .models import DEFAULT_PROVIDERS, PageFetch, ProjectConfig, SearchParams, SerpResult
from .version import cli_name, installed_version

# serp: папка проекта — по имени команды (.serp), не пакета (.rb_serp), как было до шаблона v1.2.1.
PROJECT_DIR_NAME = f".{cli_name()}"
CONFIG_FILE_NAME = "config.toml"


def find_project_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / PROJECT_DIR_NAME).is_dir():
            return candidate
    return None


def require_project_root(start: Path) -> Path:
    root = find_project_root(start)
    if root is None:
        raise ProjectNotInitialized(str(start))
    return root


def config_path(project_root: Path) -> Path:
    return project_root / PROJECT_DIR_NAME / CONFIG_FILE_NAME


def check_cli_compat(config: ProjectConfig) -> None:
    """Raise CliTooOld if this installed CLI is older than the project's `cli.min_version`.

    A project outlives any single CLI release; this is how it refuses to be
    driven by a CLI that predates a config field or command it relies on.
    Skipped for an uninstalled (bare source) run, which has no version, and
    for a build without a `v*` tag in reach (uv-dynamic-versioning's
    `0.0.0` fallback) — a dev checkout, not a release older than anything.
    """
    installed = installed_version()
    if installed is None or Version(installed).release == (0, 0, 0):
        return
    if Version(installed) < Version(config.cli.min_version):
        raise CliTooOld(installed, config.cli.min_version)


def load_config(project_root: Path, *, check_compat: bool = True) -> ProjectConfig:
    """Load and validate the project config, enforcing `cli.min_version` by default.

    Only `doctor` passes `check_compat=False` — it reports the mismatch as
    one of its checks instead of failing on it.
    """
    data = tomllib.loads(config_path(project_root).read_text())
    config = ProjectConfig.model_validate(data)
    if check_compat:
        check_cli_compat(config)
    return config


def save_config(project_root: Path, config: ProjectConfig) -> None:
    path = config_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = config.model_dump()
    if not data["prices"]:
        del data["prices"]  # пустая [prices] в новом конфиге — шум; цены дописывают руками
    path.write_text(tomli_w.dumps(data))


def init_project(project_root: Path, name: str) -> ProjectConfig:
    config = ProjectConfig(name=name)
    save_config(project_root, config)
    return config


def resolve_providers(explicit: str | None, start: Path) -> list[str]:
    """Очередь провайдеров: --provider "a,b" → `providers` проекта → по умолчанию."""
    if explicit:
        return [name.strip() for name in explicit.split(",") if name.strip()]
    root = find_project_root(start)
    if root is not None:
        return load_config(root).providers
    return list(DEFAULT_PROVIDERS)


_AGE_UNITS = {"m": "minutes", "h": "hours", "d": "days", "w": "weeks"}


def parse_age(value: str) -> timedelta:
    """'30m', '12h', '7d', '2w' → timedelta."""
    number, unit = value[:-1], value[-1:].lower()
    if not number.isdigit() or unit not in _AGE_UNITS:
        raise InvalidArguments(f"Не понимаю срок '{value}': нужно число и m/h/d/w, например 30m, 12h, 7d, 2w.")
    return timedelta(**{_AGE_UNITS[unit]: int(number)})


def collect(
    queries: list[str],
    *,
    engine: str,
    top: int,
    providers: list[str],
    yandex_lr: int | None = None,
    google_loc: str | None = None,
    use_cache: bool = False,
    max_age: timedelta | None = None,
    first_page_only: bool = False,
    on_query_done: Callable[[list[PageFetch]], None] | None = None,
) -> list[PageFetch]:
    """Страницы выдачи по запросам — последовательно, запрос за запросом.

    Каждый ответ провайдера пишется в журнал; из журнала берётся только при
    `use_cache` (и не старше `max_age`). Фолбэк на следующий провайдер — на
    уровне каждой страницы. `first_page_only` — для блоков, которые есть только
    на первой странице (реклама, похожие запросы, сводка).
    `on_query_done` вызывается после каждого запроса — для вывода прогресса.
    """
    overrides: dict = {}
    if yandex_lr is not None:
        overrides["yandex_lr"] = yandex_lr
    if google_loc is not None:
        overrides["google_loc"] = google_loc
    try:
        per_query = [SearchParams(query=query, engine=engine, top=top, **overrides) for query in queries]
    except ValidationError as exc:
        raise InvalidArguments("; ".join(err["msg"] for err in exc.errors())) from exc

    client = SerpClient(providers=providers)
    all_pages: list[PageFetch] = []
    for params in per_query:
        pages = client.fetch_sync(params, pages=1 if first_page_only else None, use_cache=use_cache, max_age=max_age)
        all_pages.extend(pages)
        if on_query_done is not None:
            on_query_done(pages)
    return all_pages


COST_CURRENCY = "RUB"


def price_env_var(provider: str) -> str:
    return re.sub(r"[^A-Z0-9]", "_", f"{cli_name()}_{provider}_price".upper())


def provider_prices(providers: list[str], start: Path) -> dict[str, float | None]:
    """Цена запроса по провайдеру: SERP_<PROVIDER>_PRICE → [prices] проекта → None (не задана)."""
    root = find_project_root(start)
    configured = load_config(root).prices if root is not None else {}
    prices: dict[str, float | None] = {}
    for provider in providers:
        name = price_env_var(provider)
        raw = os.environ.get(name)
        if raw is None:
            prices[provider] = configured.get(provider)
            continue
        try:
            prices[provider] = float(raw.strip().replace(",", "."))
        except ValueError:
            raise InvalidArguments(
                f"{name}='{raw}': цена запроса должна быть числом в рублях, например 0.25."
            ) from None
    return prices


def priced_sources(pages: list[PageFetch], prices: dict[str, float | None]) -> tuple[list[dict], float | None]:
    """`sources` со стоимостью каждой страницы и итог; из журнала — 0, цена не задана — None (и итог None).

    cost_source: "cache" — из журнала, "price_table" — цена × 1 страницу, None — цены нет.
    "provider" (фактическая стоимость из ответа) зарезервирован: XML River и XMLStock её не отдают.
    """
    rows = sources(pages)
    total: float | None = 0.0
    for row, page in zip(rows, pages, strict=True):
        if page.from_cache:
            cost, cost_source = 0.0, "cache"
        else:
            cost = prices.get(page.provider)
            cost_source = "price_table" if cost is not None else None
        row["cost"] = cost
        row["cost_currency"] = COST_CURRENCY
        row["cost_source"] = cost_source
        total = None if total is None or cost is None else total + cost
    return rows, (round(total, 6) if total is not None else None)


def sources(pages: list[PageFetch]) -> list[dict]:
    """Откуда каждая страница: провайдер, когда получена, из кэша ли."""
    return [
        {
            "query": p.query,
            "engine": p.engine,
            "page": p.page,
            "provider": p.provider,
            "fetched_at": p.fetched_at.isoformat(),
            "from_cache": p.from_cache,
            "complete": p.complete,
        }
        for p in pages
    ]


organic = organic_results


def snippets(pages: list[PageFetch], top: int) -> list[dict]:
    """Органика со всеми полями: сниппет, крошки, рейтинг, имя сайта, дата, кэш-ссылка."""
    return [
        {"serp_position": position, "query": page.query, "searchengine": page.engine, **item.model_dump()}
        for page, position, item in organic_items(pages, top)
    ]


def ads(pages: list[PageFetch]) -> list[dict]:
    return [
        {"query": page.query, "searchengine": page.engine, **ad.model_dump()}
        for page in pages
        if page.page == 0
        for ad in page.data.ads
    ]


def related(pages: list[PageFetch]) -> list[dict]:
    return [
        {"query": page.query, "searchengine": page.engine, **item.model_dump()}
        for page in pages
        if page.page == 0
        for item in page.data.related
    ]


def summary(pages: list[PageFetch]) -> list[dict]:
    """Сводка по первой странице каждого (запрос, поисковик)."""
    return [
        {
            "query": page.query,
            "searchengine": page.engine,
            "found": page.data.found,
            "ai_present": page.data.ai_present,
            "organic": len(page.data.organic),
            "ads": len(page.data.ads),
            "related": len(page.data.related),
            "knowledge_graph": page.data.knowledge_graph,
        }
        for page in pages
        if page.page == 0
    ]


def write_csv(results: list[SerpResult], stream: TextIO) -> None:
    if not results:
        return
    writer = csv.DictWriter(stream, fieldnames=list(SerpResult.model_fields))
    writer.writeheader()
    for result in results:
        writer.writerow(result.model_dump())


def save_results(results: list[SerpResult], path: Path, *, as_csv: bool) -> None:
    """Сохраняет результаты в файл: CSV или JSON-массив."""
    if as_csv:
        with path.open("w", newline="", encoding="utf-8") as stream:
            write_csv(results, stream)
    else:
        data = [result.model_dump() for result in results]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
