# AGENTS.md

Guidance for coding agents working on the **rb-serp** package (import `rb_serp`, command `serp`)
itself (not its end users — see `src/rb_serp/data/SKILL.md` for
that).

## Development

```bash
uv sync                          # also writes uv.lock — commit it
uv run serp --help
uv run scripts/release.py --check   # the whole gate — run it before every commit
```

**Before every commit, `uv run scripts/release.py --check` must pass.**
It is the authoritative gate (CI, if the repo keeps it, runs exactly this,
and every release runs it too): `uv lock --check`, ruff check + format,
ty, pytest (coverage floor, help contract + snapshots, `auth status`
shape), a changelog fragment for any change since the last tag, and a
wheel build installed into a throwaway venv where `serp
--version` must match the built version. A failure names the step and
shows its output — fix the cause, never skip the step.

Run `scripts/install_hooks.sh` once per clone: it points git at the
versioned hooks in `.githooks/` (Claude Code sessions do this themselves
via `.claude/settings.json`). The pre-commit hook **blocks** a commit where
a command's logic changed but its docstring didn't, and warns when `src/`
changed with no changelog fragment on the branch.

## Architecture (do not violate)

- `commands/` — argument parsing only. No business logic here.
- `core/` — services, models, errors. All real logic lives here.
- `providers/` — SERP providers, one module per service, each a
  `providers.SerpProvider` subclass registered in `providers.PROVIDERS`
  (`name`, `summary`, `credential_fields`, async `fetch_page(request) -> str`
  returning one page of raw Yandex.XML with every free block). Parsing
  (`core/serp_xml.py`), the response journal (`core/cache.py`), `--top`
  trimming and the fallback queue live in `SerpClient`. A provider must turn
  every vendor failure into `AuthRequired` / `ProviderError` — that is
  what `SerpClient` switches to the next provider in the queue on
  (`--provider a,b`, project `providers = [...]`, default `["xmlriver"]`).
  Tests inject `httpx.MockTransport` via the provider's `transport`
  argument. Never call `httpx` from `core/` or `commands/`.
  Every provider sets `name`, `credential_fields`, `required` and
  implements `health_check()` (one cheap, read-only, non-billed call —
  `tests/test_providers.py` fails without it). `auth status --check --json`
  runs those checks; its JSON shape is a cross-tool contract pinned by
  `tests/test_auth.py` — changing it is a breaking change.
- `credentials/` — secrets via OS keychain + env vars. Never write a secret
  to `.serp/config.toml` or any other project-local file.
- `output/` — every command supports both human and `--json` output via
  `output.emit_success` / `output.guard`. Don't `print()` directly in a
  command. stdout is data only; errors, `--verbose` progress and `logging`
  go to stderr.
- Command groups are created with `output.group("...")`, not
  `typer.Typer(...)` — that's what makes `serp <group>` with
  no subcommand print help (exit 0) instead of a usage error.
- Errors are `CliError` subclasses with a stable `code` and the fixed
  exit-code table (see `core/errors.py`). Don't raise bare exceptions as the
  intended user-facing failure path.
- Any new command that changes external state irreversibly (deletes,
  overwrites a remote resource, restarts something, ...) must support
  `--dry-run` (`output.is_dry_run`) and gate the actual action behind
  `output.require_confirmation`. Commands that only touch local project
  files (`init`, `config set`) don't need this.
- Data goes through `core/db.py` (cli-generator `architecture.md` §12).
  `SERP_DATABASE_URL` → the shared PostgreSQL, else local SQLite
  (`SERP_DB_PATH` moves the file). Caches go there too; anything kept local
  instead is listed here, with the reason.
  Tables, views, indexes: `serp_*` only, declared in `core/tables.py`
  and created by a hand-written migration in `migrations/versions/`.
  Other tools read only the `serp_v1_*` views — a new shape is a `_v2_`
  view plus a Service map entry. Cross-tool keys: `core/normalize.py`,
  stored next to the original text. Every migration must keep working
  with the previous release (expand/contract); the shared base is
  migrated only by `serp db upgrade` from a release. Append-only
  tables with a unique natural key can be marked `info={"push": ...}` for
  `serp db push` (local SQLite → `SERP_PROD_DATABASE_URL`).

## Names

Python package `rb_serp` (distribution `rb-serp`), but everything a user
sees is `serp`: the command, `SERP_<PROVIDER>_<FIELD>` env vars, keychain
entries `serp:<provider>`, the `.serp/` project dir. The command name comes
from `core.version.cli_name()` (the console script), the distribution from
`_distribution.DIST_NAME` — use those, never `__name__.split(".")[0]`, for
anything user-facing. These names are read by other tools (wordstat reads
`serp:*` / `SERP_*` via `shared_namespaces`) — don't rename them.

## Library API

`rb_serp` is also imported as a library (`from rb_serp import SerpClient,
SearchParams, SerpResult`, plus `AuthRequired` / `ProviderError`):
`await SerpClient(providers=[...]).search(params)` or `.search_sync(...)`.
Keep these names and signatures stable — other projects depend on them.
Logging is stdlib `logging` (logger `rb_serp`), never `print`.

## Releasing

- **Version = git tag.** There is no `version =` in `pyproject.toml`;
  uv-dynamic-versioning reads the latest `vX.Y.Z` tag at build time. Don't
  add one back, don't hard-code a version string anywhere.
- **Changelog = fragments.** Don't edit `CHANGELOG.md`. Every user-visible
  change adds one fragment:
  `uv run towncrier create +<slug>.<type>.md -c "One or two sentences."`
  (type: added/changed/deprecated/removed/fixed/security). The gate
  (`release.py --check`) fails without one.
- **Release** from a clean, up-to-date `main`:
  `uv run scripts/release.py patch|minor|major` — gate, towncrier build,
  commit `Release vX.Y.Z`, annotated tag. It never pushes, and neither do
  you: hand the release to the root agent (below).
- **Every user-visible change ships as a release.** Servers, other tools
  and the Mac install only from tags, never from `main`, so a merged change
  without a release is invisible to them. A change that needs a fragment is
  done only after the release: merge to `main`, run `release.py`, hand it
  to the root agent for the push, and name the new tag in the report.
  Maintenance-only commits (no fragment) wait for the next release. Don't
  leave a fragment unreleased on `main` without naming the reason.
- **SemVer is a promise about the CLI surface.** Removing/renaming/changing
  commands, flags, the `--json` shape, exit or error codes is breaking —
  major, or **minor while at `0.x`**. A new feature with old behaviour
  intact is minor (**patch while at `0.x`**); a fix is patch. Deprecate first
  (warning + a `deprecated` fragment), remove in the next breaking release.
- **Depending on another of our tools**: pin by tag in `[tool.uv.sources]`
  (`{ git = "...", tag = "vX.Y.Z" }`) with a compatible range in
  `dependencies` (`>=0.2,<0.3`) — never `branch = "main"`, never a
  committed `path =`. Bump = new tag + `uv lock --upgrade-package <name>`
  + full gate, one commit.

Projects pin the CLI they need via `[cli] min_version` in their config, so
a change a project depends on needs a release before that project can
require it.

## After changing the command surface

Agents discover commands from a bare `serp` / `--help`,
rendered from the command docstrings and `help=` strings — so the
docstring *is* the API doc. **Changed a command's behaviour → update its
docstring in the same commit**:

- docstring = one-line summary, optional paragraph, and an
  `Example: {cli} <command> ...` line (`output.guard` turns `{cli}` into
  `serp`); every `typer.Option`/`Argument` has `help=`;
- then `uv run pytest --snapshot-update` and read the diff in
  `tests/snapshots/help/` — that diff is the user-visible change.

Enforced: Claude Code hooks (`.claude/settings.json`: PostToolUse after
editing a command file, Stop before ending the turn), the pre-commit hook,
and `tests/test_help.py` in the gate. If a hook fires on a refactor that
truly doesn't change behaviour, say so — don't bypass it silently. `src/rb_serp/data/SKILL.md`
deliberately lists no commands, only conventions; touch it only if a
convention changes (a new exit code, a new credential rule).

## Inside the tools system

This tool is one service among many; the root `~/Work/tools/` agent
deploys it, keeps its credentials (Doppler) and backs up its data (S3).
Full rules: `~/Work/tools/docs/standard-release.md` §13.

- **Push and questions go through the root agent**, never straight to the
  user in this window. Release or branch ready → `herdr agent prompt
  tools-root "<repo, branch, tags, what's inside, gate result>"`; it asks
  the user and, after their yes, runs `git push --follow-tags` itself and
  tells you. Don't push yourself; force-push and `commit --amend` never. A
  question or decision for the user → the same channel, briefly: the
  question, the options, your recommendation; meanwhile do whatever doesn't
  depend on the answer.

- **Report to the root agent** (in the report to the user, next to the
  release tag): a new or renamed env var / credential, a new local database
  or changed data path, a new or dropped dependency on another tool, a
  refreshed token written back locally, a breaking release.
- **Consumers**: before a breaking change, check who uses this tool (root
  `AGENTS.md` → Service map) and name them in the report.
- **Headless Linux servers**: env vars first, keychain optional (a missing
  keychain is not an error when the env var is set); reuse existing env var
  names; no prompts without a TTY; no Mac-only paths.
- **Data** lives in the shared PostgreSQL (`SERP_DATABASE_URL`)
  or the local SQLite in `platformdirs.user_data_dir("<tool>")`, never in
  the repo; it moves between them only through `db import-sqlite` and
  `db push` (cli-generator `architecture.md` §12).
