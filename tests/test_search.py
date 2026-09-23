from __future__ import annotations

import json
from pathlib import Path

import pytest

from rb_serp.core import services
from rb_serp.core.errors import ProviderError
from rb_serp.core.models import SearchParams, SerpResult

from .test_cli import init, invoke, invoke_json


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[SearchParams, list]]:
    """Replace the network: SerpClient.search_sync returns two results per query."""
    seen: list[tuple[SearchParams, list]] = []

    def fake_search_sync(self, params: SearchParams) -> list[SerpResult]:
        seen.append((params, self._chain))
        query = params.get_queries()[0]
        return [
            SerpResult(
                serp_position=i,
                query=query,
                searchengine="yandex",
                url=f"https://d{i}.ru/",
                domain=f"d{i}.ru",
                title=f"t{i}",
            )
            for i in (1, 2)
        ]

    monkeypatch.setattr("rb_serp.core.client.SerpClient.search_sync", fake_search_sync)
    return seen


def test_search_json_envelope(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", "b", "--top", "20", "--yandex-lr", "2")
    assert code == 0
    assert payload["ok"] is True
    assert payload["count"] == 4
    assert {r["query"] for r in payload["results"]} == {"a", "b"}
    params, chain = calls[0]
    assert (params.top, params.yandex_lr, chain) == (20, 2, ["xmlriver"])


def test_search_provider_queue_from_option_and_project(calls, tmp_path: Path) -> None:
    invoke_json("--project", str(tmp_path), "search", "q", "--provider", "xmlriver, xmlriver")
    assert calls[-1][1] == ["xmlriver", "xmlriver"]

    init(tmp_path)
    invoke("--project", str(tmp_path), "config", "set", "providers", "xmlriver")
    invoke_json("--project", str(tmp_path), "search", "q")
    assert calls[-1][1] == ["xmlriver"]


def test_search_unknown_provider_is_invalid_arguments(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "q", "--provider", "nope")
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_search_top_must_be_multiple_of_ten(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "q", "--top", "15")
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_search_without_credentials_is_auth_required(tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "q")
    assert code == 3
    assert payload["error"]["code"] == "AUTH_REQUIRED"
    assert "serp auth login --provider xmlriver" in payload["error"]["action"]


def test_search_provider_failure_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def failing(self, params):
        raise ProviderError("xmlriver", "down")

    monkeypatch.setattr("rb_serp.core.client.SerpClient.search_sync", failing)
    code, payload = invoke_json("--project", str(tmp_path), "search", "q")
    assert code == 5
    assert payload["error"]["provider"] == "xmlriver"


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [("domains", "d1.ru\nd2.ru\n"), ("domains-se", "yandex\td1.ru\nyandex\td2.ru\n")],
)
def test_search_plain_formats_on_stdout(calls, tmp_path: Path, fmt: str, expected: str) -> None:
    result = invoke("--project", str(tmp_path), "search", "q", "--format", fmt)
    assert result.exit_code == 0
    assert result.stdout == expected


def test_search_csv_stdout_and_table_on_stderr(calls, tmp_path: Path) -> None:
    csv_out = invoke("--project", str(tmp_path), "search", "q", "--format", "csv")
    assert csv_out.stdout.splitlines()[0] == "serp_position,query,searchengine,url,domain,title"

    table = invoke("--project", str(tmp_path), "search", "q")
    assert table.stdout == ""
    assert "d1.ru" in table.stderr


@pytest.mark.parametrize("name", ["out.json", "out.csv"])
def test_search_output_file(calls, tmp_path: Path, name: str) -> None:
    target = tmp_path / name
    code, payload = invoke_json("--project", str(tmp_path), "search", "q", "--output", str(target))
    assert code == 0
    assert payload["saved_to"] == str(target)
    text = target.read_text(encoding="utf-8")
    if name.endswith(".json"):
        assert json.loads(text)[0]["domain"] == "d1.ru"
    else:
        assert text.startswith("serp_position,")


def test_write_csv_empty_writes_nothing(tmp_path: Path) -> None:
    services.save_results([], tmp_path / "e.csv", as_csv=True)
    assert (tmp_path / "e.csv").read_text() == ""


@pytest.mark.parametrize("command", [("search",), ("auth", "login")])
def test_help_lists_every_provider(command: tuple[str, ...]) -> None:
    from rb_serp.providers import PROVIDERS

    result = invoke(*command, "--help", env={"COLUMNS": "200"})
    for name in PROVIDERS:
        assert name in result.output
