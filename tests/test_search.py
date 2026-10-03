from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from rb_serp.core import services
from rb_serp.core.errors import ProviderError
from rb_serp.core.models import OrganicItem, PageData, PageFetch, SearchParams

from .test_cli import init, invoke, invoke_json


class Calls(list):
    """Каждый вызов SerpClient.fetch_sync: (params, очередь провайдеров, use_cache, max_age, pages)."""


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> Calls:
    """Replace the network: every page has two organic results d1.ru, d2.ru."""
    seen = Calls()

    def fake_fetch_sync(self, params: SearchParams, *, pages=None, use_cache=False, max_age=None) -> list[PageFetch]:
        seen.append((params, self._chain, use_cache, max_age, pages))
        return [
            PageFetch(
                query=query,
                engine=engine,
                page=page,
                provider="xmlriver",
                fetched_at=datetime.now(UTC),
                from_cache=use_cache,
                data=PageData(
                    organic=[
                        OrganicItem(url=f"https://d{i}.ru/", domain=f"d{i}.ru", title=f"t{i}", snippet=f"s{i}")
                        for i in (1, 2)
                    ]
                ),
            )
            for query in params.get_queries()
            for engine in params.get_engines()
            for page in range(params.num_pages if pages is None else pages)
        ]

    monkeypatch.setattr("rb_serp.core.client.SerpClient.fetch_sync", fake_fetch_sync)
    return seen


def test_search_json_envelope(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", "b", "--top", "20", "--yandex-lr", "2")
    assert code == 0
    assert payload["ok"] is True
    assert payload["count"] == 4
    assert {r["query"] for r in payload["results"]} == {"a", "b"}
    params, chain, use_cache, _, _ = calls[0]
    assert (params.top, params.yandex_lr, chain, use_cache) == (20, 2, ["xmlriver"], False)
    assert payload["sources"][0]["provider"] == "xmlriver"


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
    def failing(self, params, **kwargs):
        raise ProviderError("xmlriver", "down")

    monkeypatch.setattr("rb_serp.core.client.SerpClient.fetch_sync", failing)
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


def test_search_cache_flags(calls, tmp_path: Path) -> None:
    invoke_json("--project", str(tmp_path), "search", "q", "--cache", "--max-age", "7d")
    _, _, use_cache, max_age, _ = calls[-1]
    assert (use_cache, max_age) == (True, timedelta(days=7))

    invoke_json("--project", str(tmp_path), "search", "q", "--cache", "--force")
    assert calls[-1][2] is False

    code, payload = invoke_json("--project", str(tmp_path), "search", "q", "--cache", "--max-age", "week")
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


# --- стоимость запросов (T167) ----------------------------------------------------


def _write_prices(tmp_path: Path, prices: str) -> None:
    init(tmp_path)
    config = tmp_path / ".serp" / "config.toml"
    config.write_text(config.read_text() + f"\n[prices]\n{prices}\n")


def test_search_cost_from_project_config(calls, tmp_path: Path) -> None:
    _write_prices(tmp_path, "xmlriver = 0.25")
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", "b", "--top", "20")
    assert code == 0
    assert len(payload["sources"]) == 4
    for source in payload["sources"]:
        assert source["cost"] == 0.25
        assert source["cost_currency"] == "RUB"
    assert payload["cost_total"] == 1.0


def test_search_cost_env_overrides_config(calls, tmp_path: Path) -> None:
    _write_prices(tmp_path, "xmlriver = 0.25")
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", env={"SERP_XMLRIVER_PRICE": "0.4"})
    assert code == 0
    assert payload["sources"][0]["cost"] == 0.4
    assert payload["cost_total"] == 0.4


def test_search_cost_env_without_project(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", env={"SERP_XMLRIVER_PRICE": "0"})
    assert code == 0
    assert payload["sources"][0]["cost"] == 0.0
    assert payload["cost_total"] == 0.0


def test_search_cost_from_cache_is_zero(calls, tmp_path: Path) -> None:
    code, payload = invoke_json(
        "--project", str(tmp_path), "search", "a", "--cache", env={"SERP_XMLRIVER_PRICE": "0.3"}
    )
    assert code == 0
    assert payload["sources"][0]["from_cache"] is True
    assert payload["sources"][0]["cost"] == 0
    assert payload["cost_total"] == 0


def test_search_cost_unknown_price_is_null_with_warning(calls, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SERP_XMLRIVER_PRICE", raising=False)
    result = invoke("--project", str(tmp_path), "search", "a", "--json")
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["sources"][0]["cost"] is None
    assert payload["sources"][0]["cost_currency"] == "RUB"
    assert payload["cost_total"] is None
    assert "SERP_XMLRIVER_PRICE" in result.stderr


def test_search_cost_bad_env_price_is_invalid_arguments(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", env={"SERP_XMLRIVER_PRICE": "дорого"})
    assert code == 2
    assert payload["error"]["code"] == "INVALID_ARGUMENTS"


def test_search_help_describes_cost() -> None:
    result = invoke("search", "--help", env={"COLUMNS": "200"})
    assert "cost_total" in result.output
    assert "SERP_<PROVIDER>_PRICE" in result.output


# --- источник стоимости (T175) ----------------------------------------------------


def test_search_cost_source_price_table_from_env(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", env={"SERP_XMLRIVER_PRICE": "0.4"})
    assert code == 0
    assert payload["sources"][0]["cost_source"] == "price_table"


def test_search_cost_source_price_table_from_config(calls, tmp_path: Path) -> None:
    _write_prices(tmp_path, "xmlriver = 0.25")
    code, payload = invoke_json("--project", str(tmp_path), "search", "a")
    assert code == 0
    assert payload["sources"][0]["cost_source"] == "price_table"


def test_search_cost_source_cache(calls, tmp_path: Path) -> None:
    code, payload = invoke_json("--project", str(tmp_path), "search", "a", "--cache")
    assert code == 0
    assert payload["sources"][0]["from_cache"] is True
    assert payload["sources"][0]["cost_source"] == "cache"


def test_search_cost_source_null_without_price(calls, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("SERP_XMLRIVER_PRICE", raising=False)
    result = invoke("--project", str(tmp_path), "search", "a", "--json")
    assert result.exit_code == 0
    source = json.loads(result.stdout)["sources"][0]
    assert source["cost"] is None
    assert source["cost_source"] is None
    assert "SERP_XMLRIVER_PRICE" in result.stderr


def test_search_help_describes_cost_source() -> None:
    result = invoke("search", "--help", env={"COLUMNS": "200"})
    assert "cost_source" in result.output
    assert "price_table" in result.output
