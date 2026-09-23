from __future__ import annotations

import urllib.parse
from datetime import datetime

from pydantic import BaseModel, Field, field_validator


class SerpResult(BaseModel):
    serp_position: int
    query: str
    searchengine: str
    url: str
    domain: str
    title: str


class OrganicItem(BaseModel):
    """Результат органической выдачи со всеми полями, что отдал провайдер."""

    url: str
    domain: str
    title: str
    snippet: str = ""
    breadcrumbs: str = ""
    stars: str = ""
    site_name: str = ""
    pubdate: str = ""
    cachelink: str = ""
    contenttype: str = ""


class AdItem(BaseModel):
    """Рекламное объявление. `placement`: top / bottom / right."""

    placement: str
    position: int
    url: str
    domain: str
    title: str
    snippet: str = ""
    price: str = ""


class RelatedItem(BaseModel):
    """Похожий запрос (`kind="search"`) или похожий вопрос (`kind="question"`)."""

    kind: str
    text: str
    snippet: str = ""
    url: str = ""


class PageData(BaseModel):
    """Всё, что разобрано из одной страницы ответа."""

    found: int | None = None
    ai_present: bool | None = None
    organic: list[OrganicItem] = Field(default_factory=list)
    ads: list[AdItem] = Field(default_factory=list)
    related: list[RelatedItem] = Field(default_factory=list)
    knowledge_graph: dict[str, str] = Field(default_factory=dict)


class PageRequest(BaseModel):
    """Одна страница выдачи, которую нужно получить у провайдера."""

    query: str
    engine: str
    page: int  # с нуля; провайдер сам переводит в свою нумерацию
    yandex_lr: int = 213
    google_country: str = "RU"
    google_loc: str = "1011969"

    @property
    def region(self) -> str:
        """Регион, от которого зависит выдача этого поисковика (часть ключа кэша)."""
        return str(self.yandex_lr) if self.engine == "yandex" else self.google_loc


class PageFetch(BaseModel):
    """Одна страница выдачи: откуда пришла и что в ней."""

    query: str
    engine: str
    page: int
    provider: str
    fetched_at: datetime
    from_cache: bool
    data: PageData


class SearchParams(BaseModel):
    query: str | list[str]
    top: int = 10
    engine: str | list[str] = "yandex"
    # Страна/город (по умолчанию Россия, Москва)
    yandex_lr: int = 213  # lr=213 — Москва
    google_country: str = "RU"
    google_loc: str = "1011969"  # Москва

    @field_validator("top")
    @classmethod
    def top_must_be_valid(cls, v: int) -> int:
        if v < 10 or v > 100 or v % 10 != 0:
            raise ValueError("top должен быть кратен 10 и находиться в диапазоне 10-100")
        return v

    @field_validator("engine")
    @classmethod
    def engine_must_be_valid(cls, v: str | list[str]) -> str | list[str]:
        allowed = {"yandex", "google", "both"}
        engines = [v] if isinstance(v, str) else v
        for e in engines:
            if e not in allowed:
                raise ValueError(f"engine должен быть одним из: {allowed}")
        return v

    def get_engines(self) -> list[str]:
        """Возвращает список поисковых систем для запроса."""
        if self.engine == "both":
            return ["yandex", "google"]
        if isinstance(self.engine, list):
            result = []
            for e in self.engine:
                if e == "both":
                    result.extend(["yandex", "google"])
                else:
                    result.append(e)
            return result
        return [self.engine]

    def get_queries(self) -> list[str]:
        """Возвращает список запросов."""
        if isinstance(self.query, str):
            return [self.query]
        return self.query

    @property
    def num_pages(self) -> int:
        """Количество страниц для получения top N результатов."""
        return self.top // 10


def extract_domain(url: str) -> str:
    """Извлекает домен из URL, убирая www."""
    try:
        netloc = urllib.parse.urlparse(url).netloc
        return netloc.removeprefix("www.")
    except Exception:
        return ""


DEFAULT_PROVIDERS = ["xmlriver"]


class CliCompat(BaseModel):
    min_version: str = "0.1.0"


class ProjectConfig(BaseModel):
    version: int = 1
    name: str
    # Очередь провайдеров: первый — основной, остальные — фолбэки по порядку.
    providers: list[str] = Field(default_factory=lambda: list(DEFAULT_PROVIDERS))
    cli: CliCompat = Field(default_factory=CliCompat)

    @field_validator("providers", mode="before")
    @classmethod
    def split_providers(cls, v: object) -> object:
        # `config set providers "a,b"` приходит строкой.
        if isinstance(v, str):
            return [name.strip() for name in v.split(",") if name.strip()]
        return v
