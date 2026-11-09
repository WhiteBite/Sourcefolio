---
name: ev-feature
description: Рецепт сквозной фичи в Sourcefolio — домен-контракт, application use case с портом, адаптер, CLI-команда, экспорт JSON Schema, тесты. Use when добавляешь новую команду/операцию/отчёт/контракт в sourcefolio, "добавь команду", "новый отчёт", "новая схема", "новый operational report".
---

# Сквозная фича в Sourcefolio

Порядок шагов обязателен: пропуск ломает архитектурный тест (`tests/architecture/test_dependencies.py`) или контракт-тест (`tests/contracts/test_schemas_are_current.py`).

1. **Домен** (`domain/`): pydantic-модель, наследник `ContractModel` из `domain/common.py`. ID-типы и `UtcDateTime` брать существующие, не изобретать параллельные. Поле-версия схемы — `Literal["<feature>/v1"]`.
2. **Application** (`application/`): use case (функция или класс); внешние эффекты — только через Protocol-порт по образцу `ports.py` / `capture_ports.py`. Ни одного import из `adapters`.
3. **Адаптер** (`adapters/`): реализация порта. Subprocess — по образцу `ocr/tesseract.py`: таймаут, лимиты байт, sanitized metadata, kill дерева процессов.
4. **CLI** (`cli.py`): подкоманда argparse; handler возвращает int (exit code); вывод — JSON (`ensure_ascii=False`) или markdown. Тонкий слой: без бизнес-логики.
5. **Контракт**: `sourcefolio schema <name>` → обновить файл в `contracts/` и закоммитить вместе с моделью; тест сверяет побайтово.
6. **Тесты**: unit на каждом слое + CLI-тест в `tests/cli/`. Внешние сервисы — только fake executables через `write_fake_executable` (`tests/conftest.py`).
7. **Gate**: полный прогон (скилл ev-gate); новые тесты увеличивают baseline 125.

## Эталоны в коде

- Operational-отчёт: `application/capture_report.py` + `tests/application/test_capture_report.py` + `tests/cli/test_capture_report_command.py`.
- Контракт: `domain/extraction.py` → `contracts/extraction-record-v1.schema.json`.
- Bounded resumable операция: `application/ocr_backlog.py`, `adapters/tiktok/gallery_dl.py`.

## Инварианты фичи

- Read-only операции не получают флагов, меняющих данные.
- Отчёты immutable: каждый запуск — новый путь файла.
- Partial/failed — явные статусы с limitations/error, не пустые поля и не тихие пропуски.
- Комментарии в коде не пишутся; prose — в docs/ и commit message.
