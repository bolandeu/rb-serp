"""Shared fixtures: rerun a test file under the CI environment, and give each test its own local database."""

from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

# Before any rb_serp import: the help text names the response journal file (core.cache.help_line),
# so a path that never exists keeps the help and describe snapshots the same on every machine.
os.environ["SERP_DB_PATH"] = "/nonexistent/serp.sqlite"

import httpx
import pytest
import sqlalchemy as sa

from rb_serp.core import db
from rb_serp.providers.xmlriver import XmlRiverProvider


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


# Typer reads these once, at import, and then forces terminal output — what a CI runner sets.
CI_ENV = {"GITHUB_ACTIONS": "true", "FORCE_COLOR": "1", "PY_COLORS": "1"}


@pytest.fixture
def same_in_ci(request: pytest.FixtureRequest) -> Callable[[str], None]:
    """Rerun this file's tests matching `-k` under CI_ENV in a fresh interpreter: the only way to set a value read at import."""
    if request.config.getoption("--snapshot-update"):
        pytest.skip("the snapshots are being written; there is nothing to compare yet")

    def run(selection: str) -> None:
        options = ["-k", selection, "-p", "no:cacheprovider", "--no-cov", "-q"]
        command = [sys.executable, "-m", "pytest", str(request.path), *options]
        env = {**os.environ, **CI_ENV}
        result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)  # noqa: S603
        assert result.returncode == 0, result.stdout[-3000:]

    return run


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


# ── Контракты потребителей (tests/test_contracts.py, tests/contracts/fixtures.yaml) ──

CONTRACT_EMPTY_QUERY = "zzqx ничего не найдено"  # = placeholders.fixture_empty_query
_NO_RESULTS_XML = (
    '<?xml version="1.0" encoding="UTF-8"?><yandexsearch version="1.0"><response>'
    '<error code="15">Sorry, there are no results for this search</error></response></yandexsearch>'
)


def _contract_response(request: httpx.Request) -> httpx.Response:
    """Ответ провайдера без сети: пустая выдача (ошибка 15) для пустого запроса, иначе — запись из tests/fixtures."""
    if request.url.params.get("query") == CONTRACT_EMPTY_QUERY:
        return httpx.Response(200, text=_NO_RESULTS_XML)
    provider = "xmlstock" if request.url.host == "xmlstock.com" else "xmlriver"
    engine = "yandex" if "yandex" in request.url.path else "google"
    return httpx.Response(200, text=fixture_xml(f"{provider}_{engine}.xml"))


@pytest.fixture
def contract_offline(monkeypatch: pytest.MonkeyPatch) -> None:
    """Офлайн-данные кейсов контрактов: фальшивые ключи обоих провайдеров и MockTransport вместо сети."""
    for provider in ("XMLSTOCK", "XMLRIVER"):
        monkeypatch.setenv(f"SERP_{provider}_USER", "contract-user")
        monkeypatch.setenv(f"SERP_{provider}_TOKEN", "contract-token")
    init = XmlRiverProvider.__init__  # XmlStockProvider наследует

    def offline_init(self, credential=None, *, transport=None, **kwargs) -> None:
        init(self, credential, transport=transport or httpx.MockTransport(_contract_response), **kwargs)

    monkeypatch.setattr(XmlRiverProvider, "__init__", offline_init)
