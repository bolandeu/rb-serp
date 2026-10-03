from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa

from rb_serp.core import db


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real keychain and exported credentials out of every test."""
    for name in (
        "XMLRIVER_USER",
        "XMLRIVER_TOKEN",
        "SERP_XMLRIVER_USER",
        "SERP_XMLRIVER_TOKEN",
        "SERP_XMLSTOCK_USER",
        "SERP_XMLSTOCK_TOKEN",
        "SERP_PROVIDERS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("rb_serp.credentials.manager.keyring.get_password", lambda service, username: None)


@pytest.fixture(autouse=True)
def _local_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Every test gets its own SQLite file: never the user's database, never the shared PostgreSQL.

    Engines the test opens are disposed after it — Python 3.14 warns about every
    SQLite connection left to the garbage collector.
    """
    path = tmp_path / "db" / "test.db"
    monkeypatch.setattr(db, "sqlite_path", lambda: path)
    monkeypatch.delenv(db.ENV_VAR, raising=False)
    monkeypatch.delenv(db.PROD_ENV_VAR, raising=False)
    engines: list[sa.Engine] = []
    sqlite_engine = db.sqlite_engine

    def tracked(file: Path) -> sa.Engine:
        engines.append(sqlite_engine(file))
        return engines[-1]

    monkeypatch.setattr(db, "sqlite_engine", tracked)
    yield path
    for engine in engines:
        engine.dispose()


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
