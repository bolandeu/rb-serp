# Changelog

All notable changes to this project are documented here. Versions follow
[SemVer](https://semver.org/) (while at `0.x`, a minor bump may break).

Don't edit this file between releases: each change adds a fragment to
`changelog.d/` (`uv run towncrier create +<slug>.<type>.md -c "..."`, type
one of added/changed/deprecated/removed/fixed/security), and
`scripts/release.py` assembles them into a new section below the marker.

<!-- towncrier release notes start -->

## [0.6.0] - 2026-10-02

### Added

- `serp describe --json` — контракт инструмента в формате OpenCLI (команды, опции, коды выхода, переменные окружения, таблицы и представление `serp_v1_responses`) для каталога tools и Windmill.

### Changed

- Шаблон cli-generator v1.5.1: `describe --json`; таблица `serp_responses` помечена как кэш (`kind: cache`), представление `serp_v1_responses` объявлено в `tables.views`. Существующие команды и их вывод не менялись.


## [0.5.0] - 2026-10-01

### Changed

- Журнал ответов (кэш) — в базе инструмента: общий PostgreSQL из `SERP_DATABASE_URL` (таблица `serp_responses`, представление `serp_v1_responses`) или, без неё, локальная SQLite; при первом запуске старая таблица `responses` переносится с исходными датами получения. Новые команды `serp db status|upgrade|import-sqlite|push`; общая база недоступна или не мигрирована — ошибка до платного запроса (коды 5/1/6).


## [0.4.1] - 2026-09-27

### Changed

- Шаблон cli-generator v1.2.7: имена доступов (keychain serp:*, env SERP_*) и старые XMLRIVER_* берутся из общей логики шаблона; в auth status в source_ref указаны и старые имена переменных. Поведение и имена ключей прежние.


## [0.4.0] - 2026-09-27

### Changed

- Ломающее: `serp auth status` отдаёт общий отчёт инструментов (standard-release §8) вместо `{"provider", "authenticated"}` — все провайдеры сразу: источник, статус, остаток; `--provider` теперь фильтр (по умолчанию — все). Код 3, если xmlriver не в порядке или любой ключ неверен. `--check` проверяет ключи бесплатным запросом баланса (XML River, XMLStock) и показывает остаток в рублях; xmlstock помечен как необязательный.
- Шаблон cli-generator v1.2.0 и стандарт релизов tools: версия берётся из git-тега (`version =` в pyproject.toml больше нет), changelog собирается из фрагментов `changelog.d/`, единый гейт `uv run scripts/release.py --check`. У каждой команды в справке есть строка `Example:`; справка закреплена снапшотами.


## [0.3.0] - 2026-09-23

### Added

- Журнал всех ответов провайдеров (SQLite `serp.sqlite` в папке данных
  приложения, вся история, сжатый XML без логина и ключа); `--cache`,
  `--max-age`, `--force` у команд выдачи; `serp cache info|list|clear`.
  `--cache` берёт ответ только провайдеров из очереди `--provider`, в её
  порядке, и только полные страницы.
- Неполные страницы (меньше 8 результатов при found ≥ 100): повтор, затем
  следующий провайдер очереди, иначе лучшая из полученных с
  `complete: false` в `sources`; в журнале помечаются и из кэша не отдаются.
  `serp cache info` показывает их число, `cache list` — пометку.
- Все бесплатные блоки ответа запрашиваются и отдаются отдельными командами:
  `snippets` (сниппет, крошки, рейтинг, имя сайта, дата, кэш-ссылка), `ads`,
  `related`, `summary`; в библиотеке — `SerpClient.fetch()` / `fetch_sync()`.
- В `--json` у команд выдачи — `sources`: провайдер, время, из кэша ли.
- Где лежит журнал и сколько весит — в справке `serp` и `serp cache`.

### Fixed

- Google через XML River: страницы теперь `page=1,2…` вместо `start` — с `start`
  сервис отдавал выдачу не по запросу.
- Ровно `--top` позиций: XMLStock на Яндексе отдаёт до 14 результатов на
  страницу, лишние и дубли между страницами убираются.
- XML River по Яндексу больше не получает запрос доп. блоков: с ними сервис
  отдавал 1–8 позиций вместо 10+. Реклама Яндекса — через XMLStock.
- `--cache` отдавал ответ любого провайдера: с `-p xmlriver` приходила запись
  XMLStock (без ответов XML River на похожие вопросы).
- `--cache` отдавал неполную страницу (2–3 результата из 10) как актуальную.

## [0.2.0] - 2026-09-23

### Changed

- Одна команда `serp` (вместо `rb-serp` и `serp`); Python-пакет
  по-прежнему `rb-serp` / `from rb_serp import SerpClient, SearchParams, SerpResult`.
- CLI переведён на архитектуру cli-generator (Typer): `--json` с
  конвертом `{"ok": ..., "results": [...]}`, структурные ошибки и
  фиксированные коды выхода; `--format json` заменён на `--json`.
- Доступы XML River — в OS keychain (`serp auth login`) или env
  `SERP_XMLRIVER_USER`/`SERP_XMLRIVER_TOKEN` (старые `XMLRIVER_*` тоже
  читаются); `.env`-файлы больше не загружаются.
- Запросы к XML River идут по HTTPS (раньше логин и токен уходили по HTTP).
- Логирование: stdlib `logging` вместо loguru.

### Added

- Вся справка (`--help`, описания команд, памятка `--ai`) на русском;
  флаги автодополнения оболочки убраны.
- Очередь провайдеров с фолбэком: `search --provider a,b`, `providers` в
  `.serp/config.toml`; по умолчанию `xmlriver`.
- Список провайдеров с их возможностями в `--help` у `search` и `auth`.
- Команды `doctor`, `status`, `init`, `config`, `auth`, `skill`, флаг `--ai`.
- Провайдер `xmlstock` (XMLStock): Яндекс («Яндекс Live») и Google, тот же
  формат Яндекс.XML, что у XML River; у Google регион — Criteria ID в `lr`,
  страница — `page`. `token` — это `key` из кабинета.

### Fixed

- Сбой XML River (неверный ключ, нет денег, сеть) больше не выглядит как
  пустая выдача с exit 0: `AUTH_REQUIRED` (3) / `PROVIDER_ERROR` (5).
  Код 15 («ничего не найдено») — пустой результат без повторов.
- Заголовок с подсветкой `<hlword>` обрезался до первого подсвеченного
  слова (или был пустым) — теперь читается целиком.
- Повторный `search_sync` на одном клиенте падал на семафоре,
  привязанном к прошлому event loop, при >10 параллельных страницах.
