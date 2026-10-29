---
id: SERP-004
title: 'Cache read survives a journal row that is not zlib: skip it, use the next
  older readable row'
status: ideas
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: null
worker: claude-code
type: bugfix
owns_paths:
- src/rb_serp/core/cache.py
- tests
- changelog.d
test_paths:
- tests/test_cache.py
docs: none
tools: []
worker_args: null
class: standard
decision: false
visual: null
depends_on: []
budget_usd: null
epic: null
spec_refs: []
checks:
- uv run pytest -q
- python3 scripts/check_command_docstrings.py
resident: null
tool: null
pr: null
translation: 'Чтение кэша переживает битую строку журнала (не zlib): пропускает её
  и берёт следующую читаемую.'
---

Why: marketer T431. On the DEV PostgreSQL, `serp search --cache` fails on some `serp_responses` rows with `Error -3 while decompressing data: incorrect header check` (zlib.error out of `Cache.latest`, src/rb_serp/core/cache.py). The write format never changed (git: zlib since the first journal, 78e7325; migration 0002 only reads `xml`), so those rows were written by something else. A cache must not fail a search on one bad row.

What to build: in `Cache.latest` select the matching complete rows newest first and return the first that decodes (`zlib.decompress` then utf-8). A row that raises zlib.error/UnicodeDecodeError is skipped with one warning on stderr (id, fetched_at, provider; no XML). Bytes that are not zlib but start (after optional BOM/whitespace) with `<` are plain XML: accept them as is. If no row decodes: None (a miss, so the search refetches and writes a good row). Do not change the write path or the schema. Add a changelog fragment.

## Acceptance
- Given a journal with a newest row `xml=b"garbage"` and an older valid zlib row for the same key, When `Cache.latest` runs, Then it returns the older row. [check: tests/test_cache.py]
- Given only a corrupt row, When `Cache.latest` runs, Then it returns None and does not raise. [check: tests/test_cache.py]
- Given a row whose xml is plain `<yandexsearch>..</yandexsearch>` bytes, When `Cache.latest` runs, Then it returns that XML. [check: tests/test_cache.py]

## How to test

Unit on SQLite through the existing Cache fixtures in tests/test_cache.py (it inserts rows with zlib.compress near line 91). TDD: commit the red tests first.

## Basis

Follow Cache.latest and the insert helper in tests/test_cache.py; stderr warning like other core warnings.

## Out of scope

- No migration, no change of put(), no cleanup of prod, no new CLI command.
