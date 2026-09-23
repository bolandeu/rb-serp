# serp

Поисковая выдача Yandex/Google через SERP-провайдеров (по умолчанию [XML River](https://xmlriver.com)).
Работает как CLI (`serp search ...`) и как Python-библиотека (`from rb_serp import SerpClient`).
Python-пакет — `rb-serp` (импорт `rb_serp`), команда в терминале — `serp`.

## Установка

```bash
uv tool install "git+https://github.com/bolandeu/serp.git"
```

Одинаково на macOS, Linux и Windows; обновление — `uv tool upgrade rb-serp`, конкретная
версия — `...rb-serp.git@v0.2.0`.

**Репозиторий приватный.** `uv` ходит в git так же, как `git clone`. Если установка
падает с ошибкой авторизации — один раз `gh auth login` (или `gh auth setup-git`, если
`gh` залогинен через `--with-token`), либо SSH-вариант:

```bash
uv tool install "git+ssh://git@github.com/bolandeu/serp.git"
```

Как зависимость другого проекта: `uv add "rb-serp @ git+https://github.com/bolandeu/serp.git"`.

## Доступы

Доступы хранятся в связке ключей ОС (keychain), не в файлах проекта:

```bash
serp auth login                 # спросит user и token XML River
serp auth status                # проверить
serp doctor                     # общая диагностика: версия, проект, доступы
```

Без keychain (CI, headless Linux) — переменные окружения `SERP_XMLRIVER_USER` и
`SERP_XMLRIVER_TOKEN` (они же читаются `auth login --non-interactive`). Старые имена
`XMLRIVER_USER`/`XMLRIVER_TOKEN` тоже работают. `.env`-файлы больше не читаются.

## CLI

```bash
serp                          # версия + список команд
serp search --help            # все опции поиска
serp --ai                     # памятка для AI-агентов
```

| Опция | Коротко | По умолчанию | Описание |
| --- | --- | --- | --- |
| `--engine` | `-e` | `yandex` | `yandex` / `google` / `both` |
| `--top` | `-n` | `10` | 10..100, кратно 10 |
| `--format` | `-f` | `table` | `table` / `csv` / `domains` / `domains-se` |
| `--json` | | | конверт `{"ok": true, "count": N, "results": [...]}` в stdout |
| `--output` | `-o` | | дополнительно сохранить в файл: `.csv` → CSV, иначе JSON-массив |
| `--yandex-lr` | | `213` (Москва) | код региона Яндекса |
| `--google-loc` | | `1011969` (Москва) | Criteria ID локации Google |
| `--provider` | `-p` | `xmlriver` | очередь провайдеров через запятую, см. ниже |

```bash
serp search "купить диван"                                 # таблица
serp search "купить диван" --engine both --format domains-se
serp search "диван" "кресло" --top 20 --output results.csv
serp search "такси" --yandex-lr 2                          # Санкт-Петербург
serp search "суши" --engine both --yandex-lr 35 --google-loc 9049978
serp search "запрос" --json | jq '.results[].domain'
```

**Куда что пишется.** stdout — только данные: JSON-конверт (`--json`), CSV, домены.
Таблица `table`, статусы и логи идут в stderr. Под `--json` ошибка тоже приходит JSON-ом:
`{"ok": false, "error": {"code", "message", "action"}}`.

**Коды выхода:** `0` ок · `1` прочее · `2` неверные аргументы · `3` нужна авторизация
(`serp auth login`) · `4` проект не инициализирован · `5` сбой провайдера (сеть, деньги
на счёте, лимиты — уже с повторами) · `6` CLI старее, чем требует проект.

Пустая выдача («ничего не найдено») — это успех с `count: 0`, а не ошибка.

### Провайдеры и фолбэк

`--provider a,b,c` — очередь: для каждого запроса первый провайдер основной,
следующий пробуется только если предыдущий упал (`AUTH_REQUIRED`/`PROVIDER_ERROR`).
Отказали все → ошибка. Сейчас зарегистрирован один провайдер — `xmlriver`.

Очередь можно закрепить за проектом (папкой):

```bash
serp init                                  # создаёт .serp/config.toml
serp config set providers "xmlriver,other"
```

## Python-библиотека

```python
from rb_serp import SerpClient, SearchParams

client = SerpClient()  # очередь по умолчанию: ["xmlriver"]
# client = SerpClient(providers=["xmlriver", "other"])
results = client.search_sync(SearchParams(query="python разработка"))
# async: results = await client.search(SearchParams(...))

for r in results:
    print(r.serp_position, r.domain, r.title)
```

```python
SearchParams(
    query="запрос",  # str или list[str]
    engine="yandex",  # "yandex" | "google" | "both"
    top=10,  # 10..100, кратно 10
    yandex_lr=213,  # регион Яндекса (213 = Москва)
    google_country="RU",
    google_loc="1011969",  # Criteria ID Google (1011969 = Москва)
)
```

`SerpResult`: `serp_position` (с 1), `query`, `searchengine`, `url`, `domain` (без
`www.`), `title`.

Ошибки — исключения `rb_serp.AuthRequired` (нет/неверные доступы) и `rb_serp.ProviderError`
(сбой сервиса); у обоих есть `.message` и `.action`. Логи — стандартный `logging`,
логгер `rb_serp`: `logging.getLogger("rb_serp").setLevel(logging.ERROR)` чтобы заглушить.

Справочники регионов: [Яндекс `lr`](https://yandex.ru/dev/xml/doc/ru/reference/regions),
[Google `loc`](https://xmlriver.com/files/geo.csv).

## Новый провайдер

1. `src/rb_serp/providers/<name>.py` — подкласс `SerpProvider` с `name`,
   `credential_fields` и `async def search(params) -> list[SerpResult]`. Любой сбой
   сервиса поднимать как `AuthRequired` / `ProviderError` — на них срабатывает фолбэк.
2. Добавить класс в `PROVIDERS` в `src/rb_serp/providers/__init__.py`.

После этого он доступен в `--provider`, `serp auth login --provider <name>` и `doctor`.

## Разработка

```bash
uv sync
uv run serp --help
uv run ruff check . && uv run ruff format --check . && uv run ty check && uv run pytest
scripts/install_hooks.sh     # pre-commit: предупреждает об устаревших docstring команд
uv tool install --editable .  # глобальная команда из рабочей копии
```

`pytest` падает ниже порога покрытия из `pyproject.toml`; CI дополнительно проверяет
`uv.lock` на известные уязвимости (OSV-Scanner).

Релиз: `uv run scripts/release.py patch|minor|major` — поднимает версию, датирует
`CHANGELOG.md`, коммитит, ставит тег и пушит. Версию руками не править.
