# serp_v1_results: a table filled on write, not a view over the XML

Other tools must read every SERP that `serp` ever got, by date, without a refetch. The journal
`serp_responses` keeps each response as zlib-compressed raw XML in a binary column: SQLite and
PostgreSQL can't decompress and parse it in SQL, and the positions must come from the same Python
code as `serp search` (`core/serp_xml.parse_page`, `core/results.page_positions`). So the positions
live in their own table `serp_results`, one row per organic position, written by `SerpClient.fetch`
with the responses it journals; old responses are filled by migration 0002. The view
`serp_v1_results` over it is the read path.

Status: accepted.

## Considered options

1. A view over `serp_responses` that parses the XML in SQL — impossible: zlib in a binary column.
2. Parse on read in `serp` (a command or library call for other tools) — every reader would need
   the CLI; the platform, Directus and windmill read the database directly.
3. A parsed table filled on write plus a one-off backfill — chosen.

## The position rule

Inside one search call, per (query, engine), the call uses one page per page number: the page from
the journal cache; else the complete response; else the one with the most organic results, the
first on ties (what `SerpClient._fetch_one` returns). A stored response R at page p gets its
positions by dropping repeated URLs (`item.url` as the provider gave it) over the used pages q < p,
in order, then over R's own results; its positions continue after those pages. So retries and
incomplete responses the call did not use still get rows, positioned as if they were page p. With
`--top N`, `serp search` returns exactly the rows of the call with position <= N.

## Backfill grouping (migration 0002)

Old rows do not record their call. Per (engine, region, query_key), in `fetched_at` order, a new call
starts when the gap to the previous row is more than 10 minutes, or when the row's page already has
a complete response in the current call. A page number the call lacks is the latest complete
response with that page (same engine, region, query_key, any provider) from before the call's first
row; none — that page adds nothing. A response whose XML doesn't decompress or parse gets no rows.

## Consequences

- The table repeats data that the XML already holds: a change of the parser does not change old
  rows. A new shape or a reparse is a new migration (and a `serp_v2_results` view if columns change).
- No foreign key to `serp_responses` (`db push` forbids it): a row links to its response by
  (provider, engine, region, query, page, fetched_at). `cache clear` deletes both with one cutoff.
- A database error while writing positions is a warning: a paid search never fails for it.
