from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's real keychain and exported credentials out of every test."""
    for name in ("XMLRIVER_USER", "XMLRIVER_TOKEN", "SERP_XMLRIVER_USER", "SERP_XMLRIVER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr("rb_serp.credentials.manager.keyring.get_password", lambda service, username: None)
