"""The `auth` command group: `status` reports the health of the credentials of every provider, or an empty report when
the package has none.
"""

from __future__ import annotations

import importlib
from datetime import UTC, datetime

import typer

from .. import output
from ..core.errors import InvalidArguments
from ..core.version import DEV_VERSION, cli_name, installed_version, module_exists, optional_module
from ..providers import providers_help

# `auth status` exists in every CLI (the Standard, Health); a CLI
# generated without credentials (no credentials/ package, no commands/login.py)
# reports an empty `credentials` list and exits 0. Detected at runtime so this
# file needs no template logic — same approach as doctor.py.
_PACKAGE_NAME = __name__.split(".")[0]
_HAS_CREDENTIALS = module_exists(f"{_PACKAGE_NAME}.credentials.manager")

app = output.group("Доступы к провайдерам (ключи в связке ключей ОС).")


@app.command("status")
@output.guard
def auth_status(
    ctx: typer.Context,
    check: bool = typer.Option(
        False, "--check", help="Проверить каждый провайдер бесплатным запросом баланса (до 10 с)."
    ),
    provider: str | None = typer.Option(
        None, "--provider", help=f"Только этот провайдер (по умолчанию все). Доступны: {providers_help()}."
    ),
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Доступы всех провайдеров: откуда взяты и работают ли. Значения не печатаются.

    Без --check — только наличие (статус `unchecked` или `missing`). С --check —
    бесплатный запрос баланса у каждого провайдера: `ok`, `invalid` (ключ отклонён),
    `quota_exhausted` (баланс ≤ 0), `unreachable`; остаток в `quota` (рубли).
    xmlriver обязателен, xmlstock — запасной (optional). Код 0 — обязательные в
    порядке; 3 — обязательный не в порядке или любой ключ неверен; 2 — неизвестный
    провайдер. Форма --json — общий контракт инструментов: `tool`, `version`,
    `checked_at`, `credentials[]` с `provider`, `required`, `fields`, `source`,
    `source_ref`, `status`, `expires_at`, `quota`, `message`. `source_ref` называет
    и старые имена переменных (XMLRIVER_USER, XMLRIVER_TOKEN), если они читаются.

    Example: {cli} auth status --check --json
    """
    tool, version = cli_name(), installed_version() or DEV_VERSION
    if not _HAS_CREDENTIALS:
        if provider is not None:
            raise InvalidArguments(f"Unknown provider '{provider}' ({tool} stores no credentials).")
        checked_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        payload = {"tool": tool, "version": version, "checked_at": checked_at, "credentials": []}
        output.emit_report(ctx, payload, lambda _: output.human.echo(f"{tool} stores no credentials."), None)
        return
    health = importlib.import_module(f"{_PACKAGE_NAME}.credentials.health")
    payload, problem = health.status(tool, version, check=check, only=provider)
    output.emit_report(ctx, payload, health.render, problem)


if _login := optional_module(f"{_PACKAGE_NAME}.commands.login"):
    app.command("login")(_login.login)
    app.command("logout")(_login.logout)
