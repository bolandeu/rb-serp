"""This tool's tables, as SQLAlchemy Core — mirrors the latest migration (`migrations/versions/`).

Every name starts with `PREFIX` (`<tool>_`): the shared PostgreSQL holds every
tool's tables (and Directus') in one `public` schema. Change a table here *and* add
a migration by hand (copy the example in `migrations/versions/`) — tests/test_db.py
fails when the migrated schema and this metadata disagree.

Every table has a single-column PRIMARY KEY — Directus can't show or edit a
table without one and doesn't support a composite one: a surrogate `id`, with the
natural key as a UNIQUE constraint (tests/test_db.py checks this metadata and the
migrated database).

Every table says what its rows are, `info={"kind": "data"}` or `"cache"` (tests/test_db.py
fails without it): `data` is the tool's result and lost when deleted; `cache` is a copy
of something it can fetch again. `describe --json` publishes it (x-rb-data).

`info={"push": {...}}` marks an append-only table for `<cli> db push` (local SQLite →
production PostgreSQL): rows are inserted without their ids and skipped when one
with the same natural key exists, so the table needs a unique constraint on that key
and no foreign keys (link by natural keys). `since` names the timestamp column that
`--since` filters on.
"""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from .._distribution import TOOL

PREFIX = f"{TOOL}_"

# Deterministic constraint names: a later migration can drop or alter them by name
# on both PostgreSQL and SQLite (batch mode); they start with the (prefixed) table name.
metadata = sa.MetaData(
    naming_convention={
        "ix": "%(table_name)s_%(column_0_N_name)s_ix",
        "uq": "%(table_name)s_%(column_0_N_name)s_uq",
        "ck": "%(table_name)s_%(constraint_name)s_ck",
        "fk": "%(table_name)s_%(column_0_name)s_fk",
        "pk": "%(table_name)s_pk",
    }
)


class UtcDateTime(sa.TypeDecorator):
    """Moment in time, always aware UTC in Python: timestamptz in PostgreSQL, UTC text in SQLite.

    SQLite keeps no offset, so a naive value read back (or bound, e.g. `db push --since`)
    is UTC — never the server's local time, or dates would shift on import/push.
    """

    impl = sa.DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
        return value.replace(tzinfo=None) if dialect.name == "sqlite" else value

    def process_result_value(self, value: datetime | None, dialect: sa.Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


# Журнал всех ответов провайдеров (он же кэш): только добавление — новый ответ на тот
# же запрос — новая строка, чтение берёт самую свежую полную (core/cache.py).
responses = sa.Table(
    f"{PREFIX}responses",
    metadata,
    sa.Column("id", sa.Integer, primary_key=True),
    sa.Column("fetched_at", UtcDateTime, nullable=False, index=True),  # когда получен ответ, UTC
    sa.Column("provider", sa.Text, nullable=False),
    sa.Column("engine", sa.Text, nullable=False),
    sa.Column("region", sa.Text, nullable=False),  # lr Яндекса / loc Google
    sa.Column("query", sa.Text, nullable=False),  # как ввёл пользователь
    sa.Column("query_key", sa.Text, nullable=False),  # core.normalize.norm_phrase(query)
    sa.Column("page", sa.Integer, nullable=False),
    sa.Column("complete", sa.Boolean, nullable=False, server_default=sa.true()),
    sa.Column("xml", sa.LargeBinary, nullable=False),  # zlib(сырой XML)
    # Естественный ключ (для db push) и индекс поиска в кэше; имя явное — по
    # соглашению вышло бы длиннее 63 символов PostgreSQL.
    sa.UniqueConstraint(
        "provider", "engine", "region", "query_key", "page", "fetched_at", name="serp_responses_natural_uq"
    ),
    info={"kind": "cache", "push": {"since": "fetched_at"}},
)

# The `<tool>_v1_*` views other tools, Directus and the platform read instead of the
# tables — the tool's data contract, published by `describe --json` (x-rb-data.views).
# Declared here for that, created by a migration (`CREATE VIEW`); tests/test_db.py
# fails when a view below is missing from the migrated database or its columns differ.
# Columns never change: a different shape is a new `_v2_` view next to the old one.
# Kept out of `metadata`, which mirrors the tables only.
views = sa.MetaData()

v1_responses = sa.Table(
    f"{PREFIX}v1_responses",
    views,
    sa.Column("fetched_at", UtcDateTime, comment="Когда получен ответ, UTC"),
    sa.Column("provider", sa.Text, comment="Провайдер выдачи (xmlriver, xmlstock)"),
    sa.Column("engine", sa.Text, comment="Поисковик: yandex или google"),
    sa.Column("region", sa.Text, comment="Регион: lr Яндекса / loc Google"),
    sa.Column("query", sa.Text, comment="Запрос, как его ввёл пользователь"),
    sa.Column("query_key", sa.Text, comment="Нормализованный запрос — ключ для связи"),
    sa.Column("page", sa.Integer, comment="Номер страницы выдачи"),
    sa.Column("complete", sa.Boolean, comment="false — провайдер отдал неполную страницу"),
    comment="Журнал ответов провайдеров: строка на полученную страницу выдачи (без сырого XML)",
)
