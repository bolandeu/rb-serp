# Changelog

All notable changes to this project are documented here. The format is
[Keep a Changelog](https://keepachangelog.com/), versions follow
[SemVer](https://semver.org/). `scripts/release.py` moves the Unreleased
section under a new version heading — add entries here as you go, don't
write them at release time.

## [Unreleased]

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
