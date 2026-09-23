"""Разбор настоящих ответов сервисов (tests/fixtures — «купить диван», Москва)."""

from __future__ import annotations

from rb_serp.core.serp_xml import parse_page, response_error

from .conftest import fixture_xml


def test_xmlstock_yandex_organic_and_ads() -> None:
    page = parse_page(fixture_xml("xmlstock_yandex.xml"))
    assert len(page.organic) == 14  # XMLStock отдаёт больше 10 — обрезает клиент
    assert page.organic[0].breadcrumbs and page.organic[0].snippet
    assert page.found == 24_000_000
    assert {ad.placement for ad in page.ads} == {"top", "bottom"}
    assert page.ads[0].title and page.ads[0].domain == "lottahome.ru"


def test_xmlriver_yandex_ads_are_url_decoded() -> None:
    page = parse_page(fixture_xml("xmlriver_yandex.xml"))
    ad = page.ads[0]
    assert ad.placement == "bottom"
    assert ad.title == "Диваны купить в интернет-магазине HOFF.ru"  # было %3Cb%3EДиваны%3C/b%3E ...
    assert "%3C" not in ad.snippet


def test_xmlriver_google_related_questions_and_empty_knowledge_graph() -> None:
    page = parse_page(fixture_xml("xmlriver_google.xml"))
    assert len(page.organic) == 10
    assert [r.kind for r in page.related] == ["question"] * 4
    assert page.knowledge_graph == {}  # блок есть, но все поля пустые


def test_xmlstock_google_related_searches_and_ai_flag() -> None:
    page = parse_page(fixture_xml("xmlstock_google.xml"))
    kinds = {r.kind for r in page.related}
    assert kinds == {"search", "question"}
    assert page.ai_present is True
    assert page.organic[0].site_name


def test_response_error() -> None:
    xml = '<yandexsearch><response><error code="15">Нет результатов</error></response></yandexsearch>'
    assert response_error(xml) == (15, "Нет результатов")
    assert response_error(fixture_xml("xmlstock_google.xml")) is None
    assert parse_page(xml).organic == []
