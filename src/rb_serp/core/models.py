from __future__ import annotations

import urllib.parse

from pydantic import BaseModel, Field, field_validator


class SerpResult(BaseModel):
    serp_position: int
    query: str
    searchengine: str
    url: str
    domain: str
    title: str


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
