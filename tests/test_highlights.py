"""Подсветки выдачи: слова, которые провайдер выделил жирным в сниппете (`<hlword>`, у XML River — `<b>`)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from rb_serp.core import serp_xml, services
from rb_serp.core.models import PageFetch

ROOT = Path(__file__).resolve().parent


def _page(fixture: str):
    return serp_xml.parse_page((ROOT / "fixtures" / fixture).read_text(encoding="utf-8"))


def test_highlights_are_the_words_the_provider_bolded() -> None:
    page = _page("xmlstock_yandex.xml")

    assert page.organic, "the fixture holds organic results"
    assert any(item.highlights for item in page.organic), "XMLStock marks words with <hlword>"
    assert all(isinstance(item.highlights, tuple) for item in page.organic)


def test_a_page_without_bold_marks_has_no_highlights() -> None:
    page = _page("xmlriver_yandex.xml")

    assert page.organic and all(item.highlights == () for item in page.organic)


def test_the_snippets_answer_carries_the_highlights() -> None:
    fetched = [
        PageFetch(
            query="диван",
            engine="yandex",
            page=0,
            provider="xmlstock",
            fetched_at=datetime.now(UTC),
            from_cache=False,
            data=_page("xmlstock_yandex.xml"),
        )
    ]

    rows = services.snippets(fetched, top=10)

    assert rows and "highlights" in rows[0]
    assert any(row["highlights"] for row in rows)
