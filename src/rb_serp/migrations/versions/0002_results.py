"""serp_results (a row per organic position of every journal response) and its view serp_v1_results

Revision ID: 0002
Revises: 0001

Expand only: a new table, its indexes and view; serp_responses is untouched, so the
previous release keeps working. Then the backfill: every journal response gets its
positions, the rule of docs/adr/0001-serp-results-table.md. Old rows do not record the
search call they came from, so calls are rebuilt: per (engine, region, query_key), in
fetched_at order, a new call starts after a gap of more than `_CALL_GAP`, or when the
row's page already has a complete response in the current call. A page a call lacks
is the latest complete response of that page from before the call (any provider).

Unlike 0001, this one imports the package: the XML parser (`core.serp_xml.parse_page`),
the position rule and rb_keys (`core.results.result_rows`). The positions must equal
what `serp search` gives for the same pages, and a copy here would drift from it. Rows
are inserted through this file's own `sa.table` of the columns 0002 creates (keys of
`result_rows` outside them are dropped), so a later change of serp_results doesn't
break it. A response whose XML doesn't decompress or parse gets no rows and a warning.
"""

import logging
import zlib
from collections.abc import Iterator
from datetime import timedelta
from itertools import groupby

import sqlalchemy as sa
from alembic import op

from rb_serp.core.models import PageFetch
from rb_serp.core.results import result_rows
from rb_serp.core.serp_xml import parse_page

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

log = logging.getLogger(__name__)

_CALL_GAP = timedelta(minutes=10)
_BATCH = 500  # responses read (with their XML) per query, result rows per insert

_COLUMNS = [
    sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("engine", sa.Text, nullable=False),
    sa.Column("region", sa.Text, nullable=False),
    sa.Column("query", sa.Text, nullable=False),
    sa.Column("query_key", sa.Text, nullable=False),
    sa.Column("page", sa.Integer, nullable=False),
    sa.Column("complete", sa.Boolean, nullable=False),
    sa.Column("position", sa.Integer, nullable=False),
    sa.Column("url", sa.Text, nullable=False),
    sa.Column("url_key", sa.Text),
    sa.Column("domain", sa.Text, nullable=False),
    sa.Column("domain_key", sa.Text),
    sa.Column("title", sa.Text, nullable=False),
]


def upgrade() -> None:
    op.create_table(
        "serp_results",
        sa.Column("id", sa.Integer, primary_key=True),
        *_COLUMNS,
        sa.PrimaryKeyConstraint("id", name="serp_results_pk"),
        sa.UniqueConstraint(
            "provider", "engine", "region", "query", "page", "fetched_at", "position", name="serp_results_natural_uq"
        ),
    )
    op.create_index("serp_results_fetched_at_ix", "serp_results", ["fetched_at"])
    op.create_index("serp_results_query_key_engine_region_ix", "serp_results", ["query_key", "engine", "region"])
    # The contract with other tools: they read this view, never the table.
    op.execute(
        """
        CREATE VIEW serp_v1_results AS
        SELECT fetched_at, provider, engine, region, query, query_key, page, position,
               url, url_key, domain, domain_key, title, complete
        FROM serp_results
        """
    )
    _backfill(op.get_bind())


def downgrade() -> None:
    op.execute("DROP VIEW serp_v1_results")
    op.drop_table("serp_results")  # с ней уходят и её индексы


_responses = sa.table(
    "serp_responses",
    sa.column("id", sa.Integer),
    sa.column("fetched_at", sa.DateTime(timezone=True)),
    sa.column("provider", sa.Text),
    sa.column("engine", sa.Text),
    sa.column("region", sa.Text),
    sa.column("query", sa.Text),
    sa.column("query_key", sa.Text),
    sa.column("page", sa.Integer),
    sa.column("complete", sa.Boolean),
    sa.column("xml", sa.LargeBinary),
)
_results = sa.table("serp_results", *(sa.column(column.name, column.type) for column in _COLUMNS))


def _backfill(bind: sa.Connection) -> None:
    pending: list[dict] = []
    for (_engine, region, _query_key), group in groupby(_journal(bind), key=lambda r: r[0]):
        latest_complete: dict[int, PageFetch] = {}  # page → the latest complete response before the call
        for call in _calls(page for _key, page in group):
            used = {**latest_complete, **_used_pages(call)}
            for page in call:
                pending.extend(result_rows(region, page, used.values()))
            latest_complete.update({page.page: page for page in call if page.complete})
            if len(pending) >= _BATCH:
                _insert(bind, pending)
                pending = []
    _insert(bind, pending)


def _insert(bind: sa.Connection, rows: list[dict]) -> None:
    if rows:
        names = [column.name for column in _COLUMNS]
        bind.execute(sa.insert(_results), [{name: row[name] for name in names} for row in rows])


def _journal(bind: sa.Connection) -> Iterator[tuple[tuple[str, str, str], PageFetch]]:
    """((engine, region, query_key), parsed response) in call order; the XML is read `_BATCH` rows at a time."""
    r = _responses.c
    order = (r.engine, r.region, r.query_key, r.fetched_at, r.id)
    ids = bind.execute(sa.select(r.id).order_by(*order)).scalars().all()
    for start in range(0, len(ids), _BATCH):
        batch = bind.execute(sa.select(_responses).where(r.id.in_(ids[start : start + _BATCH])).order_by(*order))
        for row in batch:
            try:
                data = parse_page(zlib.decompress(row.xml).decode("utf-8"))
            except Exception as exc:  # any broken row is skipped, the rest go on
                log.warning("serp_results: response %s (%s, %s) not parsed: %s", row.id, row.fetched_at, row.query, exc)
                continue
            page = PageFetch(
                query=row.query, engine=row.engine, page=row.page, provider=row.provider,
                fetched_at=row.fetched_at, from_cache=False, complete=row.complete, data=data,
            )  # fmt: skip
            yield (row.engine, row.region, row.query_key), page


def _calls(pages: Iterator[PageFetch]) -> Iterator[list[PageFetch]]:
    """One group's responses split into search calls (see the module docstring)."""
    call: list[PageFetch] = []
    for page in pages:
        if call and (
            page.fetched_at - call[-1].fetched_at > _CALL_GAP
            or any(prior.page == page.page and prior.complete for prior in call)
        ):
            yield call
            call = []
        call.append(page)
    if call:
        yield call


def _used_pages(call: list[PageFetch]) -> dict[int, PageFetch]:
    """The page the call used per page number: the complete response, else the one with most results (first on ties)."""
    used: dict[int, PageFetch] = {}
    for page in call:
        best = used.get(page.page)
        if (
            best is None
            or (page.complete and not best.complete)
            or (not best.complete and len(page.data.organic) > len(best.data.organic))
        ):
            used[page.page] = page
    return used
