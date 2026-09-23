from __future__ import annotations

from pathlib import Path

import pytest


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
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("rb_serp.credentials.manager.keyring.get_password", lambda service, username: None)


@pytest.fixture(autouse=True)
def isolated_cache(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """Every test gets its own empty response journal instead of ~/Library/Caches/serp."""
    db_path = tmp_path / "serp-journal" / "serp.sqlite"
    monkeypatch.setenv("SERP_DB_PATH", str(db_path))
    return db_path


FIXTURES = Path(__file__).parent / "fixtures"


def fixture_xml(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")
