from __future__ import annotations

import typer

from .. import output
from ..core.errors import InvalidArguments
from ..credentials import manager as credentials
from ..providers import provider_class

app = output.group("Manage credentials.")

ProviderOption = typer.Option("xmlriver", "--provider", help="Provider name (see `serp search --help`).")


@app.command("login")
@output.guard
def login(
    ctx: typer.Context,
    provider: str = ProviderOption,
    non_interactive: bool = output.NonInteractiveOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Store a provider's credential fields in the OS keychain.

    Each field is taken from SERP_<PROVIDER>_<FIELD> (e.g. SERP_XMLRIVER_USER,
    SERP_XMLRIVER_TOKEN) when set, otherwise prompted for. With
    --non-interactive, a missing field is an error instead of a prompt.
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
    """Remove a provider's credential from the OS keychain."""
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
    provider: str = ProviderOption,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
) -> None:
    """Check whether every credential field of a provider is available (env or keychain)."""
    authenticated = provider_class(provider).load_credential() is not None
    output.emit_success(
        ctx,
        {"provider": provider, "authenticated": authenticated},
        render_human=lambda d: output.human.echo(
            f"{'Authenticated' if d['authenticated'] else 'Not authenticated'} for '{d['provider']}'."
        ),
    )
