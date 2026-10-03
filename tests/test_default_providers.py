"""Очередь провайдеров по умолчанию: -p → конфиг проекта → SERP_PROVIDERS → xmlstock,xmlriver."""

from __future__ import annotations

from pathlib import Path

import pytest

from rb_serp import SearchParams, SerpClient
from rb_serp.core import services
from rb_serp.core.models import DEFAULT_PROVIDERS

from .test_cli import init, invoke, invoke_json
from .test_providers import Stub


def test_default_queue_is_xmlstock_then_xmlriver(tmp_path: Path) -> None:
    assert DEFAULT_PROVIDERS == ["xmlstock", "xmlriver"]
    assert services.resolve_providers(None, tmp_path) == ["xmlstock", "xmlriver"]
    assert SerpClient(cache=False)._chain == ["xmlstock", "xmlriver"]


def test_env_overrides_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERP_PROVIDERS", " xmlriver , xmlstock ,")
    assert services.resolve_providers(None, tmp_path) == ["xmlriver", "xmlstock"]
    assert SerpClient(cache=False)._chain == ["xmlriver", "xmlstock"]


def test_blank_env_means_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERP_PROVIDERS", " , ")
    assert services.resolve_providers(None, tmp_path) == ["xmlstock", "xmlriver"]


def test_option_and_project_beat_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SERP_PROVIDERS", "xmlriver")
    assert services.resolve_providers("xmlstock", tmp_path) == ["xmlstock"]

    init(tmp_path)
    invoke("--project", str(tmp_path), "config", "set", "providers", "xmlstock")
    assert services.resolve_providers(None, tmp_path) == ["xmlstock"]
    assert services.resolve_providers("xmlriver,xmlstock", tmp_path) == ["xmlriver", "xmlstock"]


def test_unknown_provider_in_env_is_invalid_arguments(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "q", env={"SERP_PROVIDERS": "nope"})
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_cache_finds_old_xmlriver_answers_with_new_default() -> None:
    """Журнал до смены умолчания — ответы xmlriver; --cache по новой очереди их находит, без сети и доступов."""
    SerpClient(Stub("xmlriver")).search_sync(SearchParams(query="q"))
    pages = SerpClient().fetch_sync(SearchParams(query="q"), use_cache=True)
    assert (pages[0].from_cache, pages[0].provider) == (True, "xmlriver")


@pytest.mark.parametrize("command", ["search", "snippets"])
def test_help_names_default_and_env(command: str) -> None:
    text = " ".join(invoke(command, "--help", env={"COLUMNS": "200"}).output.split())
    assert "SERP_PROVIDERS" in text
    assert "xmlstock,xmlriver" in text
