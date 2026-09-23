from __future__ import annotations

import csv
import json
import tomllib
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

import tomli_w
from packaging.version import Version
from pydantic import ValidationError

from .client import SerpClient
from .errors import CliTooOld, InvalidArguments, ProjectNotInitialized
from .models import DEFAULT_PROVIDERS, ProjectConfig, SearchParams, SerpResult
from .version import CLI_NAME, installed_version

# Derived at import time from this package's own name, so this module never
# needs template substitution: it just works under whatever package_name
# the project was generated with.
PROJECT_DIR_NAME = f".{CLI_NAME}"
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
    Skipped for an uninstalled (bare source) run, which has no version.
    """
    installed = installed_version()
    if installed is None:
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
    path.write_text(tomli_w.dumps(config.model_dump()))


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


def search(
    queries: list[str],
    *,
    engine: str,
    top: int,
    providers: list[str],
    yandex_lr: int | None = None,
    google_loc: str | None = None,
    on_query_done: Callable[[list[SerpResult]], None] | None = None,
) -> list[SerpResult]:
    """Поиск по запросам последовательно (по одному запросу за вызов провайдера).

    Фолбэк на следующий провайдер срабатывает на уровне отдельного запроса.
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
    all_results: list[SerpResult] = []
    for params in per_query:
        results = client.search_sync(params)
        all_results.extend(results)
        if on_query_done is not None:
            on_query_done(results)
    return all_results


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
