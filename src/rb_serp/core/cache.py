"""Журнал всех ответов провайдеров — он же кэш.

Каждая полученная страница выдачи пишется сюда всегда (вся история, сжатый
сырой XML). Ключ — поисковик, регион, запрос, страница и провайдер: у
провайдеров разные доп. блоки (у XML River — ответы на похожие вопросы), так
что ответ одного не подменяет другой. Читать из кэша вызывающий решает сам
(`serp search --cache`): берётся самая свежая *полная* запись по ключу —
неполная страница (`complete = false`) хранится для истории, но не отдаётся.

Хранится в базе инструмента (core/db.py, таблица `serp_responses`): общий
PostgreSQL из `SERP_DATABASE_URL` или локальная SQLite. Только добавление —
повторный ответ на тот же запрос не заменяет старый, а ложится рядом со своей
датой получения (`fetched_at`, UTC).

Рядом, в `serp_results`, — позиции органики каждого записанного ответа
(`put_results`, считает core/results.py); читают их через `serp_v1_results`.
`clear` удаляет их вместе с ответами.

Почему своя таблица, а не готовый кэш: hishel ключует по URL — а в нём логин и
ключ провайдера; diskcache — это key→value без истории и выборки по дате.
Нужны история, поиск по дате и размер — это SQL-таблица.
"""

from __future__ import annotations

import logging
import zlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import sqlalchemy as sa
from pydantic import BaseModel

from . import db
from .normalize import norm_phrase
from .tables import responses, results

log = logging.getLogger(__name__)


def human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("Б", "КБ", "МБ"):
        if size < 1024:
            return f"{size:.0f} {unit}"
        size /= 1024
    return f"{size:.1f} ГБ"


def files_size(path: Path) -> int:
    """Размер базы вместе с журналом WAL (-wal, -shm); 0, если базы ещё нет."""
    return sum(p.stat().st_size for p in path.parent.glob(path.name + "*")) if path.parent.is_dir() else 0


def _location() -> str:
    """Где журнал — без открытия базы и без пароля."""
    url = db.database_url()
    return sa.make_url(url).render_as_string(hide_password=True) if url else str(db.sqlite_path())


def help_line() -> str:
    """Строка для --help: где журнал и сколько весит — без открытия базы."""
    if db.database_url():
        return f"Журнал ответов: {_location()}."
    size = files_size(db.sqlite_path())
    return f"Журнал ответов: {_location()} — {human_size(size) if size else 'пока пуст'}."


class CachedPage(BaseModel):
    xml: str
    provider: str
    fetched_at: datetime


class CacheEntry(BaseModel):
    id: int
    fetched_at: datetime
    provider: str
    engine: str
    region: str
    query: str
    page: int
    complete: bool
    size: int


class CacheStats(BaseModel):
    path: str
    file_size: int
    entries: int
    incomplete: int
    oldest: datetime | None
    newest: datetime | None
    by_provider: dict[str, int]


class ResponseCache:
    """Журнал в базе инструмента; `path` — отдельный файл SQLite вместо неё (тесты, библиотека)."""

    def __init__(self, path: Path | None = None) -> None:
        self._file = path
        self._engine: sa.Engine | None = None
        self.path = str(path) if path else _location()

    @property
    def engine(self) -> sa.Engine:
        """База открывается один раз, при первом обращении: SQLite мигрируется, PostgreSQL проверяется."""
        if self._engine is None:
            if self._file is None:
                self._engine = db.open_db()
            else:
                engine = db.sqlite_engine(self._file)
                try:
                    db.upgrade(engine)
                except Exception:
                    engine.dispose()
                    raise
                self._engine = engine
            self.path = db.describe(self._engine) if self._file is None else str(self._file)
        return self._engine

    def close(self) -> None:
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None

    def put(
        self, *, provider: str, engine: str, region: str, query: str, page: int, xml: str, complete: bool = True
    ) -> datetime:
        fetched_at = datetime.now(UTC)
        with self.engine.begin() as conn:
            conn.execute(
                sa.insert(responses).values(
                    fetched_at=fetched_at, provider=provider, engine=engine, region=region, query=query,
                    query_key=norm_phrase(query), page=page, complete=complete,
                    xml=zlib.compress(xml.encode("utf-8")),
                )
            )  # fmt: skip
        return fetched_at

    def put_results(self, rows: list[dict]) -> None:
        """Позиции органики ответов журнала (core/results.result_rows) — одной транзакцией."""
        if not rows:
            return
        with self.engine.begin() as conn:
            conn.execute(sa.insert(results), rows)

    def latest(
        self,
        *,
        provider: str,
        engine: str,
        region: str,
        query: str,
        page: int,
        max_age: timedelta | None = None,
    ) -> CachedPage | None:
        """Самая свежая полная запись этого провайдера по ключу, или None."""
        c = responses.c
        sql = sa.select(c.id, c.xml, c.provider, c.fetched_at).where(
            c.provider == provider,
            c.engine == engine,
            c.region == region,
            c.query_key == norm_phrase(query),
            c.page == page,
            c.complete.is_(True),
        )
        if max_age is not None:
            sql = sql.where(c.fetched_at >= _cutoff(max_age))
        with self.engine.connect() as conn:
            for row in conn.execute(sql.order_by(c.fetched_at.desc(), c.id.desc())):
                try:
                    xml = _decode(row.xml)
                except (zlib.error, UnicodeDecodeError):
                    log.warning(
                        "Запись журнала %s (%s, %s) не читается — беру следующую", row.id, row.fetched_at, row.provider
                    )
                    continue
                return CachedPage(xml=xml, provider=row.provider, fetched_at=row.fetched_at)
        return None

    def stats(self) -> CacheStats:
        c = responses.c
        with self.engine.connect() as conn:
            count, complete, oldest, newest = conn.execute(
                sa.select(
                    sa.func.count(),
                    sa.func.count().filter(c.complete.is_(True)),
                    sa.func.min(c.fetched_at),
                    sa.func.max(c.fetched_at),
                )
            ).one()
            by_provider = dict(conn.execute(sa.select(c.provider, sa.func.count()).group_by(c.provider)).all())
            if db.is_postgres(conn):
                size = conn.execute(sa.select(sa.func.pg_total_relation_size(responses.name))).scalar_one()
            else:
                size = files_size(Path(self.engine.url.database or ""))
        return CacheStats(
            path=self.path,
            file_size=size,
            entries=count,
            incomplete=count - complete,
            oldest=_utc(oldest),
            newest=_utc(newest),
            by_provider=by_provider,
        )

    def entries(self, *, query: str | None = None, limit: int = 50) -> list[CacheEntry]:
        """Последние записи журнала (без XML), свежие первыми; `query` — подстрока запроса."""
        c = responses.c
        sql = sa.select(
            c.id, c.fetched_at, c.provider, c.engine, c.region, c.query, c.page, c.complete,
            sa.func.length(c.xml).label("size"),
        )  # fmt: skip
        if query:
            sql = sql.where(c.query_key.contains(norm_phrase(query), autoescape=True))
        with self.engine.connect() as conn:
            rows = conn.execute(sql.order_by(c.fetched_at.desc()).limit(limit)).mappings().all()
        return [CacheEntry(**row) for row in rows]

    def count_older(self, older_than: timedelta | None) -> int:
        """Сколько записей удалит `clear` с тем же аргументом (None — все)."""
        sql = sa.select(sa.func.count()).select_from(responses)
        if older_than is not None:
            sql = sql.where(responses.c.fetched_at < _cutoff(older_than))
        with self.engine.connect() as conn:
            return conn.execute(sql).scalar_one()

    def clear(self, older_than: timedelta | None) -> int:
        """Удалить записи старше `older_than` (None — все) с их позициями; в SQLite — и вернуть место на диске."""
        cutoff = None if older_than is None else _cutoff(older_than)
        with self.engine.begin() as conn:
            deleted = conn.execute(_older(responses, cutoff)).rowcount
            conn.execute(_older(results, cutoff))  # serp_v1_results не показывает удалённую историю
        if not db.is_postgres(self.engine):
            with self.engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
                conn.execute(sa.text("VACUUM"))  # иначе файл не уменьшится
        return deleted


def _older(table: sa.Table, cutoff: datetime | None) -> sa.Delete:
    """DELETE строк `table`, полученных до `cutoff` (None — всех)."""
    sql = sa.delete(table)
    return sql if cutoff is None else sql.where(table.c.fetched_at < cutoff)


def _decode(data: bytes) -> str:
    """XML записи: сжатый zlib (так пишет `put`) или несжатый, если байты начинаются с `<`."""
    try:
        return zlib.decompress(data).decode("utf-8")
    except zlib.error:
        text = data.decode("utf-8-sig").lstrip()
        if text.startswith("<"):
            return text
        raise


def _cutoff(age: timedelta) -> datetime:
    return datetime.now(UTC) - age


def _utc(value: datetime | None) -> datetime | None:
    """min/max через агрегат теряют тип колонки: в SQLite приходят наивной строкой/датой без поправки."""
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
