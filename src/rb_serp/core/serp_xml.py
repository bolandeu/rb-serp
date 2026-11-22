"""Разбор ответа в формате Яндекс.XML (`<yandexsearch>`) — его отдают и XML River, и XMLStock."""

from __future__ import annotations

import re
import urllib.parse
import xml.etree.ElementTree as ET

from .models import AdItem, OrganicItem, PageData, RelatedItem, extract_domain

_TAG = re.compile(r"<[^>]+>")
_ADS_PLACEMENTS = {"topads": "top", "bottomads": "bottom", "rightads": "right"}


def _text(el: ET.Element | None) -> str:
    """Текст элемента целиком (с <hlword>), без лишних пробелов, HTML-тегов и %-кодирования."""
    if el is None:
        return ""
    text = "".join(el.itertext())
    if "%3C" in text or "%3c" in text:  # XML River отдаёт заголовки рекламы URL-кодированными
        text = urllib.parse.unquote(text)
    return " ".join(_TAG.sub("", text).split())


def response_error(xml_text: str) -> tuple[int | None, str] | None:
    """(код, текст) из <error code="...">, или None, если ошибки нет."""
    error = ET.fromstring(xml_text).find(".//error")  # noqa: S314 — ответ доверенного API
    if error is None:
        return None
    code = error.get("code")
    return (int(code) if code and code.isdigit() else None), _text(error)


_NO_RESULTS = 15


def _highlights(doc: ET.Element) -> tuple[str, ...]:
    """Слова, на которых провайдер поставил подсветку: `<hlword>` в сниппете и пассажах, у XML River — `<b>`.

    Порядок — как в выдаче, повторы убраны: один и тот же подсвеченный оборот приходит с каждым пассажем страницы.
    """
    words: list[str] = []
    for holder in ("passages", "snippet"):
        for tag in ("hlword", "b"):
            for node in doc.findall(f".//{holder}//{tag}"):
                text = _text(node).strip()
                if text and text not in words:
                    words.append(text)
    return tuple(words)


def parse_page(xml_text: str) -> PageData:
    root = ET.fromstring(xml_text)  # noqa: S314 — ответ доверенного API
    error = root.find(".//error")
    no_results = error is not None and error.get("code") == str(_NO_RESULTS)

    docs = [group.find("doc") for group in root.findall(".//group")]
    if not any(doc is not None for doc in docs):
        docs = root.findall(".//doc")
    organic = [
        OrganicItem(
            url=url,
            domain=extract_domain(url),
            title=_text(doc.find("title")),
            snippet=_text(doc.find("passages")) or _text(doc.find("headline")),
            highlights=_highlights(doc),
            breadcrumbs=_text(doc.find("breadcrumbs")),
            stars=_text(doc.find("stars")),
            site_name=_text(doc.find("site_name")),
            pubdate=_text(doc.find("pubDate")),
            cachelink=_text(doc.find("cachelink")),
            contenttype=_text(doc.find("contenttype")),
        )
        for doc in docs
        if doc is not None and (url := _text(doc.find("url")))
    ]

    ads: list[AdItem] = []
    for tag, placement in _ADS_PLACEMENTS.items():
        for position, ad in enumerate(root.findall(f".//{tag}/query"), start=1):
            url = _text(ad.find("url"))
            ads.append(
                AdItem(
                    placement=placement,
                    position=position,
                    url=url,
                    domain=extract_domain(url),
                    title=_text(ad.find("title")),
                    snippet=_text(ad.find("snippet")),
                    price=_text(ad.find("price")),
                )
            )

    related = [
        RelatedItem(kind="search", text=text)
        for q in root.findall(".//relatedSearches/query")
        if (text := _text(q.find("title")) or _text(q))
    ]
    related += [
        RelatedItem(kind="question", text=text, snippet=_text(item.find("snippet")), url=_text(item.find("url")))
        for item in root.findall(".//relatedQuestions/item")
        if (text := _text(item.find("question")))
    ]

    graph = root.find(".//knowledge_graph")
    knowledge_graph = {child.tag: value for child in graph if (value := _text(child))} if graph is not None else {}

    found = _text(root.find(".//response/found"))
    ai = _text(root.find(".//ai/present"))
    return PageData(
        no_results=no_results,
        found=int(found) if found.isdigit() else None,
        ai_present=(ai == "1") if ai else None,
        organic=organic,
        ads=ads,
        related=related,
        knowledge_graph=knowledge_graph,
    )
