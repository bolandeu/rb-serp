---
name: serp
description: Conventions for driving the serp CLI from an agent — install, JSON output, exit codes, credentials, destructive-command gating. Invoke when a task involves this project's operations.
---

# serp

Everything command-specific is in the CLI itself — run `serp`
with no arguments for version + command list, `serp <command>
--help` for details. This document only covers what `--help` can't tell
you. If you can run the CLI but not read files, `serp --ai`
prints this document.

## Install (if `serp --version` fails)

```bash
uv tool install "git+https://github.com/bolandeu/serp.git"
```

Same command on macOS, Linux and Windows; `uv tool upgrade rb-serp`
updates it. Do not run from source unless asked to develop the CLI.

The repository may be private. On an auth error (`Authentication failed`,
`could not read Username`, `Repository not found`) do not ask for a token:
run `gh auth status`; if it passes, `gh auth setup-git` and retry; if not,
ask the user to run `gh auth login`. With SSH keys configured, this works
without any of that:

```bash
uv tool install "git+ssh://git@github.com/bolandeu/serp.git"
```

## Conventions

- **`--json` on every command.** Parse stdout; never scrape human output.
  stdout is data only, stderr is diagnostics.
- **Project-aware.** Commands find the project by walking up from the
  current directory, or take `--project PATH`. Not initialized →
  `serp init --non-interactive`.
- **`--non-interactive`** on anything that could prompt. It fails with a
  structured error instead of hanging.
- **Credentials** live in the OS keychain or env vars
  (`SERP_<PROVIDER>_<FIELD>`), never in arguments or
  config. Check `auth status --json`; log in with `auth login`; never ask
  the user to paste a secret into a command line.
- **Destructive commands** are the ones whose `--help` shows `--dry-run`
  and `--confirm`. Always: run with `--dry-run`, show the user, get
  explicit sign-off, then `--confirm --non-interactive`. Never chain
  straight to `--confirm` without a human having seen the dry-run.
- **Providers are a queue.** `--provider a,b` (or `providers` in the
  project config): the first answers, the rest are fallbacks tried in
  order only if the previous one failed. Default is `xmlriver`.
- **Searches cost money** (billed per query/page by the provider). Don't
  re-run the same query in a loop — save with `--output` and reuse it.
- **`doctor --json`** first when something looks off — it reports
  project, config, version compatibility and credential readiness.

## Errors

Error envelope: `{"ok": false, "error": {"code", "message", "action"}}`.
Read exit code → `error.code` → `error.action` (the concrete next command).

    0  ok
    1  general (UNEXPECTED = a bug: re-run with --verbose, report; ABORTED = user said no)
    2  invalid arguments
    3  auth required          → serp auth login
    4  project not initialized → serp init
    5  provider error (network / external service; already retried) → retry later
    6  CLI too old for this project → uv tool upgrade rb-serp

Only exit 5 is worth an automatic retry.
