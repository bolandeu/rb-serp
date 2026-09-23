"""serp — поисковая выдача Yandex/Google через SERP-провайдеров: библиотека и CLI."""

from .core.client import SerpClient
from .core.errors import AuthRequired, CliError, ProviderError
from .core.models import SearchParams, SerpResult

__all__ = ["AuthRequired", "CliError", "ProviderError", "SearchParams", "SerpClient", "SerpResult"]
