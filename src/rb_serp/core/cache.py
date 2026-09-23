"""Журнал всех ответов провайдеров — он же кэш.

Каждая полученная страница выдачи пишется сюда всегда (вся история, сжатый
сырой XML). Ключ — поисковик, регион, запрос, страница и провайдер: у
провайдеров разные доп. блоки (у XML River — ответы на похожие вопросы), так
что ответ одного не подменяет другой. Читать из кэша вызывающий решает сам
(`serp search --cache`): берётся самая свежая *полная* запись по ключу —
неполная страница (`complete = 0`) хранится для истории, но не отдаётся.

Почему sqlite3 из стандартной библиотеки, а не готовый кэш: hishel ключует по
URL — а в нём логин и ключ провайдера; diskcache — это key→value без истории
и выборки по дате. Нужны история, поиск по дате и размер — это SQL-таблица.
"""

from __future__ import annotations

import contextlib
import os
import sqlite3
import zlib
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import platformdirs
from pydantic import BaseModel

from .version import CLI_NAME

_SCHEMA = """
CREATE TABLE IF NOT EXISTS responses (
    id INTEGER PRIMARY KEY,
    fetched_at TEXT NOT NULL,
    provider TEXT NOT NULL,
    engine TEXT NOT NULL,
    region TEXT NOT NULL,
    query TEXT NOT NULL,
    query_key TEXT NOT NULL,
    page INTEGER NOT NULL,
    complete INTEGER NOT NULL DEFAULT 1,
    xml BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS responses_lookup ON responses (provider, engine, region, query_key, page, fetched_at);
CREATE INDEX IF NOT EXISTS responses_date ON responses (fetched_at);
"""


def default_path() -> Path:
    """`SERP_DB_PATH` или папка данных приложения (~/Library/Application Support/serp на macOS).

    Не папка кэша: это журнал всей истории, а Caches чистят ОС и программы-уборщики.
    """
    explicit = os.environ.get(f"{CLI_NAME.upper()}_DB_PATH")
    return (
        Path(explicit)
        if explicit
        else Path(platformdirs.user_data_dir(CLI_NAME, appauthor=False)) / f"{CLI_NAME}.sqlite"
    )


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


def help_line() -> str:
    """Строка для --help: где журнал и сколько весит — без открытия базы."""
    path = default_path()
    size = files_size(path)
    return f"Журнал ответов: {path} — {human_size(size) if size else 'пока пуст'}."


def query_key(query: str) -> str:
    """Регистр и лишние пробелы на выдачу не влияют — и на ключ тоже."""
    return " ".join(query.lower().split())


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
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_path()

    @contextlib.contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Соединение на одну операцию: commit при успехе, rollback при ошибке, всегда close."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30)
        try:
            conn.execute("PRAGMA journal_mode=WAL")  # несколько serp одновременно — без блокировок
            conn.executescript(_SCHEMA)
            _migrate(conn)
            with conn:
                yield conn
        finally:
            conn.close()

    def put(
        self, *, provider: str, engine: str, region: str, query: str, page: int, xml: str, complete: bool = True
    ) -> datetime:
        fetched_at = datetime.now(UTC)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO responses (fetched_at, provider, engine, region, query, query_key, page, complete, xml)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (fetched_at.isoformat(), provider, engine, region, query, query_key(query), page, int(complete),
                 zlib.compress(xml.encode("utf-8"))),
            )  # fmt: skip
        return fetched_at

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
        sql = (
            "SELECT xml, provider, fetched_at FROM responses"
            " WHERE provider=? AND engine=? AND region=? AND query_key=? AND page=? AND complete=1"
        )
        args: list = [provider, engine, region, query_key(query), page]
        if max_age is not None:
            sql += " AND fetched_at >= ?"
            args.append(_cutoff(max_age))
        with self._connect() as conn:
            row = conn.execute(sql + " ORDER BY fetched_at DESC LIMIT 1", args).fetchone()
        if row is None:
            return None
        return CachedPage(
            xml=zlib.decompress(row[0]).decode("utf-8"), provider=row[1], fetched_at=datetime.fromisoformat(row[2])
        )

    def stats(self) -> CacheStats:
        with self._connect() as conn:
            count, incomplete, oldest, newest = conn.execute(
                "SELECT COUNT(*), COUNT(*) - COALESCE(SUM(complete), 0), MIN(fetched_at), MAX(fetched_at) FROM responses"
            ).fetchone()
            by_provider = dict(conn.execute("SELECT provider, COUNT(*) FROM responses GROUP BY provider").fetchall())
        size = files_size(self.path)
        return CacheStats(
            path=str(self.path),
            file_size=size,
            entries=count,
            incomplete=incomplete,
            oldest=datetime.fromisoformat(oldest) if oldest else None,
            newest=datetime.fromisoformat(newest) if newest else None,
            by_provider=by_provider,
        )

    def entries(self, *, query: str | None = None, limit: int = 50) -> list[CacheEntry]:
        """Последние записи журнала (без XML), свежие первыми; `query` — подстрока запроса."""
        sql = "SELECT id, fetched_at, provider, engine, region, query, page, complete, LENGTH(xml) FROM responses"
        args: list = []
        if query:
            sql += " WHERE query_key LIKE ?"
            args.append(f"%{query_key(query)}%")
        with self._connect() as conn:
            rows = conn.execute(sql + " ORDER BY fetched_at DESC LIMIT ?", [*args, limit]).fetchall()
        return [
            CacheEntry(
                id=r[0], fetched_at=datetime.fromisoformat(r[1]), provider=r[2], engine=r[3], region=r[4],
                query=r[5], page=r[6], complete=bool(r[7]), size=r[8],
            )
            for r in rows
        ]  # fmt: skip

    def count_older(self, older_than: timedelta | None) -> int:
        """Сколько записей удалит `clear` с тем же аргументом (None — все)."""
        with self._connect() as conn:
            if older_than is None:
                return conn.execute("SELECT COUNT(*) FROM responses").fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM responses WHERE fetched_at < ?", [_cutoff(older_than)]
            ).fetchone()[0]

    def clear(self, older_than: timedelta | None) -> int:
        """Удалить записи старше `older_than` (None — все) и вернуть место на диске."""
        with self._connect() as conn:
            if older_than is None:
                deleted = conn.execute("DELETE FROM responses").rowcount
            else:
                deleted = conn.execute("DELETE FROM responses WHERE fetched_at < ?", [_cutoff(older_than)]).rowcount
        with self._connect() as conn:
            conn.execute("VACUUM")  # иначе файл не уменьшится
        return deleted


def _migrate(conn: sqlite3.Connection) -> None:
    """База без колонки `complete` (до 0.3.0): добавить и посчитать по сохранённым ответам."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(responses)")}
    if "complete" in columns:
        return
    from .serp_xml import parse_page  # только для миграции

    with conn:
        conn.execute("ALTER TABLE responses ADD COLUMN complete INTEGER NOT NULL DEFAULT 1")
        for row_id, blob in conn.execute("SELECT id, xml FROM responses").fetchall():
            complete = parse_page(zlib.decompress(blob).decode("utf-8")).complete
            conn.execute("UPDATE responses SET complete=? WHERE id=?", (int(complete), row_id))


def _cutoff(age: timedelta) -> str:
    return (datetime.now(UTC) - age).isoformat()
