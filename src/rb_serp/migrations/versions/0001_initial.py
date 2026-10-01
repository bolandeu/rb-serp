"""serp_responses (journal of provider answers, the cache) and its v1 view

Revision ID: 0001
Revises:

A local SQLite from serp <= 0.4 has the journal as `responses` (created by the old
code, no Alembic): its rows move into `serp_responses` with their original
`fetched_at`, the key recomputed with norm_phrase (adds NFKC and ё→е), and the old
table is dropped. Only on SQLite — in the shared PostgreSQL a `responses` table, if
any, is not ours.

Names are literal on purpose: a migration is history and must not change when
code it would import does — norm_phrase is copied below for the same reason.
"""

import unicodedata
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _norm_phrase(text: str) -> str:
    """core.normalize.norm_phrase as of 0001."""
    text = unicodedata.normalize("NFKC", text).lower().replace("ё", "е")
    return " ".join(text.split())


def upgrade() -> None:
    bind = op.get_bind()
    responses = op.create_table(
        "serp_responses",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("engine", sa.Text, nullable=False),
        sa.Column("region", sa.Text, nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("query_key", sa.Text, nullable=False),
        sa.Column("page", sa.Integer, nullable=False),
        sa.Column("complete", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column("xml", sa.LargeBinary, nullable=False),
        sa.PrimaryKeyConstraint("id", name="serp_responses_pk"),
        sa.UniqueConstraint(
            "provider", "engine", "region", "query_key", "page", "fetched_at", name="serp_responses_natural_uq"
        ),
    )
    op.create_index("serp_responses_fetched_at_ix", "serp_responses", ["fetched_at"])

    if bind.dialect.name == "sqlite" and sa.inspect(bind).has_table("responses"):
        legacy_columns = {column["name"] for column in sa.inspect(bind).get_columns("responses")}
        # До 0.3.0 колонки complete не было: такие ответы считаются полными.
        complete = "complete" if "complete" in legacy_columns else "1"
        rows = bind.execute(
            sa.text(
                f"SELECT fetched_at, provider, engine, region, query, page, {complete}, xml"  # noqa: S608
                " FROM responses ORDER BY id"
            )
        ).fetchall()
        op.bulk_insert(
            responses,
            [
                {
                    # ISO с поправкой (+00:00) → наивное UTC, как его хранит SQLAlchemy в SQLite
                    "fetched_at": datetime.fromisoformat(r[0]).astimezone(UTC).replace(tzinfo=None),
                    "provider": r[1],
                    "engine": r[2],
                    "region": r[3],
                    "query": r[4],
                    "query_key": _norm_phrase(r[4]),
                    "page": r[5],
                    "complete": bool(r[6]),
                    "xml": r[7],
                }
                for r in rows
            ],
        )
        op.drop_table("responses")  # с ним уходят и его индексы

    # The contract with other tools: they read this view, never the table. Its
    # columns don't change — a different shape is a new `serp_v2_` view next to it.
    # Без xml: это сжатый сырой ответ, разобранная выдача — будущий serp_v1_results.
    op.execute(
        """
        CREATE VIEW serp_v1_responses AS
        SELECT fetched_at, provider, engine, region, query, query_key, page, complete FROM serp_responses
        """
    )


def downgrade() -> None:
    op.execute("DROP VIEW serp_v1_responses")
    op.drop_table("serp_responses")
