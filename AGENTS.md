# AGENTS.md — Evidence Vault

Local-first архив доказательств для сохранённых TikTok-видео и слайдшоу: immutable captures, derived OCR, lexical search, evidence packs, генерация Obsidian vault. Python 3.12+, pydantic v2, SQLite control plane, модульный монолит ports & adapters. Prose в проекте на русском; код и идентификаторы на английском.

## Команды

Первый сетап (Windows; на Linux/macOS — `.venv/bin/` вместо `.venv\Scripts\`):

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev,media,slides]"
```

Полный gate — те же четыре шага, что и CI (`.github/workflows/ci.yml`, матрица 3.12/3.13):

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

Ожидаемый baseline: ruff без замечаний, mypy strict **80 source files**, pytest — весь набор зелёный (лёгкие предупреждения от тестов `warnings` допустимы). Smoke CLI:

```powershell
.\.venv\Scripts\evidence-vault.exe archive-capture examples/manual-capture/manifest.json --archive-dir data/archive
```

## Do / Don't

- Do run the full gate before every commit: `ruff format --check .`, `ruff check .`, `mypy src`, `pytest`.
- Do keep the layer contract: `domain` never imports `application`/`adapters`; `application` never imports `adapters`.
- Do regenerate `contracts/*.schema.json` with `evidence-vault schema <name>` after touching a pydantic model, and commit both together.
- Do treat `data/` and `.opencode/dejavu/` as local-only: never commit them.
- Don't add code comments; prose lives in `docs/` and commit messages.
- Don't refactor, rename or reformat outside the scope of the task.
- Don't bump the version, tag or publish without an explicit request.

## Структура

```text
src/evidence_vault/
  domain/        # контракты, ID-типы, инварианты; без инфраструктуры
  application/   # use cases + Protocol-порты; не знает адаптеров
  adapters/      # filesystem, sqlite, tiktok (export/yt-dlp/gallery-dl), ocr
  cli.py         # тонкий входной адаптер: argparse + сборка зависимостей
contracts/       # экспортированные JSON Schema (сверяются тестом побайтово)
docs/            # PRODUCT, ARCHITECTURE, DOMAIN, ROADMAP, PILOT, ADR, pilots
examples/        # только synthetic-примеры
stubs/           # .pyi для внешних зависимостей (mypy_path = src:stubs)
tests/           # domain / application / adapters / cli / contracts / architecture
data/            # локальные данные (gitignored): archive, derived, control, vault
```

## Где искать

| Задача | Место |
|---|---|
| Продукт и границы системы | docs/PRODUCT.md, docs/ROADMAP.md |
| Архитектура, плоскости данных | docs/ARCHITECTURE.md, docs/DOMAIN.md |
| Capture и identity (source_id/capture_id/art_) | domain/source.py, ADR 0003 |
| Новая CLI-команда | cli.py + application/ + tests/cli/ |
| JSON Schema контракта | contracts/*.schema.json; источник — pydantic-модель |
| Operational-отчёты | application/capture_report.py, ocr_backlog_report.py |
| Obsidian-генерация | application/source_cards.py, obsidian_views.py |
| Пилот на личных данных | docs/PILOT.md, HANDOFF.md §5 |
| Производительность и лимиты | docs/PERFORMANCE.md |

## Жёсткие правила

- Слои: `domain` не импортирует `application`/`adapters`; `application` не импортирует `adapters`. Enforcement — AST-тест `tests/architecture/test_dependencies.py`.
- Контракты: `model_json_schema()` pydantic-модели обязан побайтово совпадать с файлом в `contracts/`. После правки модели перегенерировать через `evidence-vault schema <name>` и закоммитить оба. Тест: `tests/contracts/test_schemas_are_current.py`.
- Captures неизменяемы: `source_id` (логический источник), `capture_id` (content-derived), `artifact_id = art_<sha256>`. Повтор capture идемпотентен; новая версия не затирает прежнюю.
- Не смешивать `creator_claim`, `agent_inference`, `external_observation`, `user_outcome`. Универсального trust score нет и не добавлять.
- Partial capture обязан явно перечислять limitations; ошибка загрузки живёт в control plane, а не в source packet.
- Derived (OCR/extraction records) пишутся только в `data/derived/`, raw packet не мутируется.
- `archive-audit` и `capture-report` read-only: orphan'ы выводятся в отчёт, ничего не удаляется.
- Obsidian: генерация только в `data/vault/_generated/evidence-vault/`; ручные заметки не перезаписываются.
- Релевантность ≠ истинность: evidence-pack собирает ссылки и excerpts, автоматический ответ не создаёт.
- Тесты не ходят в сеть: живой TikTok и настоящий Tesseract запрещены, только fake executables.

## Анти-паттерны

- Комментарии в коде не пишутся; prose живёт в docs/ и commit messages.
- Рефактор и переименования вне рамки задачи запрещены.
- Не коммитить `data/` (личный export, cookies, медиа) и `.opencode/dejavu/`.
- Зависимости не расширять без нужды: ijson, pydantic, pyyaml, tzdata (win32); yt-dlp и gallery-dl — optional extras.

## Windows-специфика (машина разработки)

- Fake-исполняемые в тестах — только через `write_fake_executable` из `tests/conftest.py` (posix: shebang; win32: `.cmd`-обёртка). Прямой `write_text` с shebang даст WinError 193.
- Fake-скрипты с не-ASCII выводом обязаны начинаться с `sys.stdout.reconfigure(encoding="utf-8")` — иначе cp1251 в пайпах.
- Убийство подпроцессов: posix — `os.killpg(SIGKILL)` при spawn с `start_new_session=True`; win32 — `taskkill /F /T /PID` (иначе сироты держат хэндлы логов и temp dir не удаляется).
- `zoneinfo` на win32 требует pip-пакет `tzdata` — уже в зависимостях с маркером `sys_platform == "win32"`.
- Платформенное сужение для mypy — только `sys.platform != "win32"` (typeshed-условия), не `os.name`.

## Session start

Чтобы вспомнить контекст в начале сессии: прочитай `data/vault/_generated/evidence-vault/context/index.md` (machine-версия — `context.json`), либо выполни `evidence-vault context`. Бриф собирается из профилей проектов (`data/vault/projects/`), накопленных совпадений `project-fit` и решений (`data/vault/decisions/`). MCP-инструмент `context` делает то же, но требует перезапуска opencode после изменения `opencode.json`.

## Handoff-контекст

Проект привезён из облачного sandbox архивом. `HANDOFF_MANIFEST.json` — снимок 154 файлов с SHA-256 от 2026-11-03, baseline 125 tests / ruff clean / mypy strict. При переносе были повреждены `src/evidence_vault/__init__.py` и `application/metrics.py`, восстановлены по контракту потребителей (коммит 228ad34). Манифест — историческая запись, не живой чек-лист.

## Следующие этапы (docs/ROADMAP.md)

Пилот на реальном TikTok export; human-reviewed OCR golden set; 10 judged-вопросов для search; ASR/keyframes через тот же derived contract. GraphRAG и второй wiki-compiler — только после измеренных результатов.
