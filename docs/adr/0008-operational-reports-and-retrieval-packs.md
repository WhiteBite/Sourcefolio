# ADR 0008: Operational reports, retrieval packs и generated-only Obsidian views

- Дата: 2026-11-03
- Статус: accepted

## Контекст

После появления derived OCR нужно обрабатывать backlog партиями, наблюдать ingestion и собирать проверяемый контекст для вопросов. Опасности: создать второй несовместимый OCR runner, смешать operational state со знанием, выдать lexical retrieval за evidence-backed answer или начать перезаписывать Obsidian notes.

## Решение

1. `capture-report` читает существующие SQLite `capture_jobs`, `capture_attempts` и `task_json` без миграции схемы. Это read-only operational projection с retries, finished-attempt p50/p95, stale candidates и sanitized recent errors.
2. Общий application `OcrBacklogRunner` обслуживает `ocr-capture` и `ocr-backlog`. Batch selection поддерживает filters, latest-per-source по умолчанию, capture/slide limits; execution использует bounded chunks/workers, failure isolation и request-ID resume. Persistent OCR queue и multiprocess leases не вводятся без отдельного gate.
3. `evidence-pack` — rebuildable retrieval-only JSON/Markdown. Он содержит ranked captures, snippets, raw artifact links, derived excerpts, extraction/request IDs, typed locators, provenance и limitations, но не automatic answer, verification или новые canonical claims.
4. `project-obsidian` создаёт OCR-enriched source cards и overview только внутри `_generated/evidence-vault/`. Human-owned notes и immutable raw packets не изменяются.

## Последствия

- Одинаковые resume/failure semantics применяются к одиночному и batch OCR.
- Operational metrics не попадают в knowledge plane; retrieval score не становится trust score.
- Evidence pack можно передать на ручной review или будущему answer-generator, но его наличие не доказывает ответ.
- Overview и cards можно удалить и пересобрать; их generator versions/properties отражают derived coverage.
- Batch OCR пока не обещает crash-safe distributed scheduling или глобальный RSS limit.

## Gate

На реальном корпусе проверить OCR throughput/RSS/failure mix, качество excerpts и locators, время review и 10 реальных judged questions. Live media/Tesseract acceptance не заменяется fake-tool smoke.
