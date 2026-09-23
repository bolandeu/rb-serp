# Changelog

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/). `scripts/release.py` moves the Unreleased
section under a new version heading — add entries here as you go, don't
write them at release time.

## [Unreleased]

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
