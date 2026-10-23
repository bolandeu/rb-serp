---
id: SERP-002
title: Make help and describe snapshots independent of the machine (data dir)
status: in_progress
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: architect
worker: claude-code
type: chore
owns_paths:
- tests/conftest.py
- tests/snapshots
test_paths: []
docs: none
tools: []
worker_args: null
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- uv run pytest -q
resident: null
tool: null
pr: null
---

Why: the remote CI gate (Linux, user root) fails test_describe_snapshot, test_describe_is_the_same_in_ci, test_help_snapshot[cache], test_help_is_the_same_in_ci. The snapshots tests/snapshots/describe.json and tests/snapshots/help/cache.txt hold the Mac data dir line 'Журнал ответов: /Users/bolandau/Library/Application Support/serp/serp.sqlite — 5 МБ.' (core.cache.help_line, built at import time from the real user data dir and the size of the real file).
Do: at the top of tests/conftest.py, before any rb_serp import, set os.environ['SERP_DB_PATH'] to a fixed path that never exists (for example /nonexistent/serp.sqlite), so help_line is the same on every machine ('... — пока пуст.'). Check db.PATH_ENV_VAR in core/db.py for the real variable name. Then run 'uv run pytest --snapshot-update', read the snapshot diff: only that line in describe.json and help/cache.txt (and other help files that print help_line) may change. Then run FORCE_COLOR=1 uv run pytest -q with HOME set to a temp dir to prove it does not depend on the machine.

## Read first

- `src/rb_serp/core/cache.py`
- `src/rb_serp/core/db.py`

## Out of scope

- No change to src/, no change to test files other than tests/conftest.py, no new commands.
