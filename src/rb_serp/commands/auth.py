from __future__ import annotations

import typer
from rich.markup import escape

from .. import output, providers
from ..core.errors import CredentialsUnhealthy, InvalidArguments
from ..core.version import CLI_NAME, DEV_VERSION, installed_version
from ..credentials import health
from ..credentials import manager as credentials
from ..providers import provider_class, providers_help

app = output.group("Доступы к провайдерам (ключи в связке ключей ОС).")

ProviderOption = typer.Option("xmlriver", "--provider", help=f"Провайдер. Доступны: {providers_help()}.")


@app.command("login")
@output.guard
def login(
    ctx: typer.Context,
    provider: str = ProviderOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Сохранить доступы провайдера в связку ключей ОС.

    Каждое поле берётся из переменной SERP_<PROVIDER>_<FIELD> (например
    SERP_XMLSTOCK_USER, SERP_XMLSTOCK_TOKEN), если она задана, иначе
    спрашивается. С --non-interactive недостающее поле — ошибка, а не вопрос.

    Example: {cli} auth login --provider xmlriver
    """
    fields = provider_class(provider).credential_fields
    values = credentials.read_fields_from_env(provider, list(fields))
    missing = [field for field in fields if not values.get(field)]
    if missing and (ctx.obj.get("non_interactive") or non_interactive):
        env_names = ", ".join(f"SERP_{provider.upper()}_{field.upper()}" for field in missing)
        raise InvalidArguments(f"Missing {env_names} in the environment and --non-interactive was set.")
    for field in missing:
        values[field] = typer.prompt(f"{provider} {field}", hide_input=field != "user")
    credentials.store_credential(provider, values)
    output.emit_success(
        ctx,
        {"provider": provider},
        render_human=lambda d: output.human.echo(f"Stored credential for '{d['provider']}'."),
    )


@app.command("logout")
@output.guard
def logout(
    ctx: typer.Context,
    provider: str = ProviderOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Удалить доступы провайдера из связки ключей ОС.

    Example: {cli} auth logout --provider xmlstock
    """
    provider_class(provider)
    credentials.delete_credential(provider)
    output.emit_success(
        ctx,
        {"provider": provider},
        render_human=lambda d: output.human.echo(f"Removed credential for '{d['provider']}'."),
    )


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
    `source_ref`, `status`, `expires_at`, `quota`, `message`.

    Example: {cli} auth status --check --json
    """
    known = providers.all_providers()
    selected = health.select(known, provider)
    if provider is not None and not selected:
        names = ", ".join(f"{cls.name}:<target>" if cls.per_target else cls.name for cls in known) or "none"
        raise InvalidArguments(f"Unknown provider '{provider}' (known: {names}).")
    payload = health.report(CLI_NAME, installed_version() or DEV_VERSION, selected, check=check, only=provider)
    problems = {entry["provider"]: entry["status"] for entry in payload["credentials"] if health.is_problem(entry)}
    output.emit_report(ctx, payload, _render_status, CredentialsUnhealthy(problems) if problems else None)


def _render_status(payload: dict) -> None:
    if not payload["credentials"]:
        output.human.echo("No providers with credentials.")
    for entry in payload["credentials"]:
        mark = {"ok": "[green]✓[/green]", "unchecked": "[dim]·[/dim]"}.get(entry["status"], "[red]✗[/red]")
        optional = "" if entry["required"] else " (optional)"
        detail = f" — {entry['message']}" if entry["message"] else ""
        expires = f", expires {entry['expires_at']}" if entry["expires_at"] else ""
        line = f"{entry['provider']}{optional}: {entry['status']} ({entry['source']}: {entry['source_ref']}){expires}{detail}"
        output.human.echo(f"{mark} {escape(line)}")
