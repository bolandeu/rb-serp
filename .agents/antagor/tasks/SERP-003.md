---
id: SERP-003
title: 'serp_v1_results: one row per SERP position of every journal response (table
  filled on write + backfill)'
status: in_review
attempts: 0
blocked: false
blocked_reason: null
phase: null
tests_hash: null
publish_stage: null
created_by: architect
approved_by: architect
worker: claude-code
type: feature
owns_paths:
- src/rb_serp/core
- src/rb_serp/migrations/versions
- tests
- pyproject.toml
- uv.lock
- docs/adr
- AGENTS.md
- README.md
- src/rb_serp/commands/cache.py
test_paths:
- tests/test_results.py
docs:
- docs/adr/0001-serp-results-table.md
- AGENTS.md
- README.md
tools: []
worker_args: null
class: hard
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
translation: 'Витрина serp_v1_results: строка на позицию каждого ответа журнала; таблица
  serp_results заполняется при записи, старые ответы — миграцией 0002; ключи — rb-keys.'
---

Why: other tools (windmill `serp_search`, scheduled positions, competitor pages, clustering) must read every SERP that `serp` ever got from one place, by date, without a refetch. Today the journal `serp_responses` keeps only zlib-compressed raw XML; `serp_v1_responses` has no positions. Consumer task: marketer T339 / card /Users/bolandau/Work/marketer/tasks/S338-serp-full.md (item «витрина serp_v1_results»).

Decision (Architect): a parsed table filled on write + a backfill in the migration, NOT an SQL view over the XML: the XML is zlib in a binary column, SQLite and PostgreSQL cannot decompress and parse it in SQL, and the parse must be the same Python code as `serp search` (`core/serp_xml.parse_page`). Record this as ADR `docs/adr/0001-serp-results-table.md` (format: docs/adr/README.md) with the reasons and the backfill grouping rule below.

## What to build

1. Dependency `rb-keys>=0.2,<0.3` (our package; `[tool.uv.sources] rb-keys = { index = "gitea" }`, the `gitea` index already exists in pyproject.toml), then `uv lock`. Keys come only from `rb_keys.norm_query` / `norm_url` / `norm_domain` (they equal the CLI `keys query|url|domain`). `norm_url`/`norm_domain` raise ValueError on a non-URL: store NULL then. Do not change `core/normalize.py` and do not change `serp_responses.query_key` (it is the cache key; out of scope).

2. Table `serp_results` in `core/tables.py` (same style as `responses`: no schema=, `info={"kind": "cache", "push": {"since": "fetched_at"}}`): `id` PK; `fetched_at` UtcDateTime, `provider`, `engine`, `region`, `query`, `page`, `complete` — copied from the response row; `query_key` = rb_keys.norm_query(query); `position` int; `url`, `domain` (as `serp search` gives it: `OrganicItem.domain`), `title`; `url_key`, `domain_key` nullable. Natural key UNIQUE (provider, engine, region, query, page, fetched_at, position), explicit short name `serp_results_natural_uq` (63-char limit). Index on (query_key, engine, region) and on fetched_at. No foreign key (push forbids it): the row is linked to its response by (provider, engine, region, query, page, fetched_at).

3. View `serp_v1_results`, declared in `tables.views` with `comment=` on every column, columns in this order: fetched_at, provider, engine, region, query, query_key, page, position, url, url_key, domain, domain_key, title, complete. `CREATE VIEW serp_v1_results AS SELECT <those> FROM serp_results`. `describe --json` then lists it in `x-rb-data.views` by itself (check the snapshot diff).

4. Positions = exactly `serp search --json` (`serp_position`, `url`, `domain`, `title`), WITHOUT the `--top` cut: a search with `--top N` equals the view rows with position <= N. The rule (factor it out of `client.organic_items` so both use ONE function): inside one search call, per (query, engine): for every page number take the page the call used (a page from the journal cache; else the complete response; else the one with the most organic items, the first on ties — the same choice `_fetch_one` makes); a stored response R at page p gets positions by dedup (raw `item.url`, as today) over the used pages q < p in order, then R's own items; positions continue after the used pages q < p. So retries and incomplete responses that the call did not use still get rows, positioned as if they were page p.

5. On write: `SerpClient.fetch` keeps a list of the call's stored responses (every `_save` that wrote a row, with region, parsed data, fetched_at, complete) and the pages it took from the journal. In a `finally` after the TaskGroup (a failed page must not lose the rows of the pages that were paid), it computes the rows by rule 4 and writes them with a new `ResponseCache.put_results(...)` in one transaction. A DB error there is a warning, like `_save` (never break a paid search). Rows only for responses written in this call; never rewrite rows of a cached response. `cache=False` → nothing.

6. Backfill in a new migration `0002` (expand only; `down_revision = "0001"`): create the table, indexes and view, then fill `serp_results` for every existing `serp_responses` row, in batches (prod PostgreSQL holds thousands of rows; do not load all XML at once). Calls are not recorded in old rows, so group them: per (engine, region, query_key of the response), rows in fetched_at order; a new call starts when the gap to the previous row is > 10 minutes, or when the row's page already has a complete response in the current call. A page number a call lacks: use the latest complete response with that page (same engine, region, query_key, any provider) fetched before the call's first row; none → that page adds nothing. Then rule 4. A row whose XML does not decompress or parse: no result rows, a log warning, go on (tests store `b"x"`). The migration may import the parser and rb_keys from the package (write in its docstring why: the parse must equal search's), but it inserts through its own literal `sa.table(...)` of the columns 0002 creates, so later table changes do not break it. Downgrade drops view and table.

7. `ResponseCache.clear(older_than)` deletes the matching `serp_results` rows too (same cutoff on fetched_at), so the view never shows deleted history.

8. Docs: the ADR; one line in AGENTS.md «Architecture» next to the journal (where results are written, that the view is the read path); README if it lists tables/views. Update the docstring of any command whose behaviour changes (likely none).

## Acceptance

- Given a call `SerpClient.fetch` with top=20 on mocked providers (fixtures in tests/fixtures/, a duplicate URL on page 2), When it finishes, Then `serp_v1_results` rows of that call equal `organic_results(pages, 20)` (position, url, domain, title) and keys equal rb_keys functions. [check: tests/test_results.py]
- Given an incomplete page then a complete retry, When stored, Then both responses have rows, positions of the unused one continue after page q<p. [check: tests/test_results.py]
- Given a journal with rows from the old release (several calls by date, a duplicate across pages, a broken XML), When migration 0002 runs on SQLite, Then each response has its rows, different days are different rows, the positions equal search's for the same pages. [check: tests/test_results.py]
- Given TEST_DATABASE_URL from scripts/pg_test.sh, When the PostgreSQL test runs, Then the view exists with its columns and the backfill and on-write rows are there. [check: tests/test_results.py]
- Given journal rows of two dates with results, When `serp cache clear --older-than` deletes the old responses, Then their result rows are gone and the new ones stay. [check: tests/test_results.py]

## How to test

Unit and integration on SQLite through the existing fixtures (`_local_database`, `httpx.MockTransport` providers as in tests/test_search.py and tests/test_cache.py); the migration test builds a DB at revision 0001 (alembic `command.upgrade(cfg, "0001")`), inserts journal rows with real XML from tests/fixtures (zlib), then upgrades to head. PostgreSQL only via `scripts/pg_test.sh`, run detached (`scripts/run_detached.sh`). TDD: commit the red tests first, keep the red run output for the report.

Existing tests that must change because a new table exists (report each with this reason): tests/test_db.py `test_push`, `test_import_sqlite`, `test_postgres_round_trip` compare the whole per-table dict and now also see `serp_results`; extend the expected dict, do not weaken the asserts. Snapshot updates (`describe.json`) are expected: read the diff. Change no other existing test; if one fails, stop and report.

## Basis

client.organic_items/organic_results (position rule), cache.ResponseCache (journal writes), migrations/versions/0001_initial.py (migration style, view creation), tables.py (declarations and views)

## Read first

- `src/rb_serp/core/client.py`
- `src/rb_serp/core/cache.py`
- `src/rb_serp/migrations/versions/0001_initial.py`
- `scripts/pg_test.sh`
- `scripts/run_detached.sh`

## Out of scope

- No change to serp_responses, its query_key, core/normalize.py, serp_v1_responses
- No new command and no change to the --json of existing commands
- No snippets, ads, related queries in the view

## Feedback

### Answer to question #1

> tests/test_cache.py::test_legacy_journal_moves_to_serp_responses_with_its_dates asserts get_view_names() == ['serp_v1_responses']; migration 0002 adds serp_v1_results, so this existing test fails, and the task lists only three tests in test_db.py as allowed to change.
> Question: May I change this assert to include serp_v1_results?
> Options:
> 1. Change the assert to ['serp_v1_responses', 'serp_v1_results'] and report it with the reason (new view)
> 2. Leave the test red and stop
> Recommended: Change the assert to ['serp_v1_responses', 'serp_v1_results'] and report it with the reason (new view)

Option 1: change the assert to include serp_v1_results and report it with the reason (a new view).
