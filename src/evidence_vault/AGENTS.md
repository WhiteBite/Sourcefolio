# src/evidence_vault — слои и контракты

Направление зависимостей: `domain ◀ application ◀ adapters ◀ cli`. Нарушение ловит AST-тест `tests/architecture/test_dependencies.py` (domain не видит application/adapters; application не видит adapters).

## Слои

- `domain/` — pydantic-контракты и инварианты. `common.py`: `ContractModel`, `NonEmptyStr`, `UtcDateTime`, `Provenance`, `canonical_json`. ID-типы (`SourceId`, `CaptureId`, `ArtifactId`) и правила identity. Никакой инфраструктуры: filesystem, subprocess, sqlite запрещены.
- `application/` — use cases (`ArchiveCapture`, `CaptureBatchRunner`, `OcrBacklogRunner`, `EvidencePack`) и Protocol-порты (`ports.py`, `capture_ports.py`, `extraction_ports.py`, `projection_ports.py`). Адаптеры выбираются снаружи; здесь только интерфейсы и orchestration.
- `adapters/` — реализации портов: `filesystem/` (content-addressed store, атомарная запись), `sqlite/capture_state.py` (jobs/attempts/resume), `tiktok/` (export parser, yt-dlp video path, gallery-dl slideshow resolver), `ocr/tesseract.py` (subprocess OCR с лимитами), `metadata_only.py`.
- `cli.py` — argparse: парсит аргументы, собирает адаптеры с use case'ами, печатает JSON/Markdown. Бизнес-логики здесь нет.

## Правила

- Файл в `contracts/` — это экспорт pydantic-модели (`evidence-vault schema <name>`); источник истины — код, файл генерируется и сверяется тестом.
- Внешние процессы (yt-dlp, gallery-dl, tesseract): таймаут, лимиты байт/слайдов, sanitized metadata по allowlist, kill дерева процессов при таймауте (killpg на posix, `taskkill /F /T` на win32).
- Новый тип extraction → `domain/extraction.py` (typed locators) → derived storage; raw packet не мутируется.
- strict mypy: suppressions запрещены; для сторонних пакетов без типов — `.pyi` в `stubs/`.
