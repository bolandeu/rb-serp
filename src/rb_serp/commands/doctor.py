"""The `doctor` command: checks of this CLI's own setup, one line per check."""

from __future__ import annotations

import typer

from .. import output
from ..core import deps
from ..core.version import DEV_VERSION, installed_version, optional_module

_PACKAGE_NAME = __name__.split(".")[0]


@output.guard
def doctor(
    ctx: typer.Context,
    json_output: bool = output.JsonOption,
    quiet: bool = output.QuietOption,
    verbose: bool = output.VerboseOption,
) -> None:
    """Диагностика: версия, проект, настройки, база, связка ключей, доступы провайдеров.

    Доступы — офлайн, по наличию (как `auth status` без --check).

    Всегда завершается с кодом 0 — результат это сам отчёт; `healthy`
    говорит, прошли ли все проверки.

    Example: {cli} doctor --json
    """
    checks: list[dict] = [{"name": "python", "ok": True}]
    checks.append({"name": "cli_version", "ok": True, "detail": installed_version() or DEV_VERSION})

    if project := optional_module(f"{_PACKAGE_NAME}.commands.project"):  # include_project=false drops it
        checks.extend(project.doctor_checks(ctx))

    if credentials := optional_module(f"{_PACKAGE_NAME}.credentials.health"):  # include_auth=false drops it
        checks.extend(credentials.doctor_checks())
    checks.extend(deps.doctor_checks())  # [[tool.rb.external]] in pyproject.toml
    if db := optional_module(f"{_PACKAGE_NAME}.core.db"):  # include_db=false drops it at generation time
        checks.extend(db.doctor_checks())

    all_ok = all(check["ok"] for check in checks)
    output.emit_success(
        ctx, {"healthy": all_ok, "checks": checks}, render_human=lambda data: output.human.render_doctor(data["checks"])
    )
