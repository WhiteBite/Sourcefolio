# ADR 0009: Воспроизводимая OCR-оценка и отдельная pack integrity

- Дата: 2026-11-03
- Статус: accepted

## Контекст

После внедрения batch OCR нужно сравнивать версии на golden samples и проверять, что retrieval pack действительно ссылается на сохранённые доказательства. Нельзя считать отсутствие record доказанным OCR failure, смешивать версии extractor без указания выбора или называть ссылочную целостность фактической проверкой.

## Решение

1. Каждый `ocr-capture`/`ocr-backlog` может сохранить immutable `ocr-backlog-report/v1` по новому пути. Report содержит query/runtime/extractor parameters и per-item created/skipped/failed с реальными request/extraction IDs там, где record существует.
2. Golden `ocr-evaluation/v1` использует stable capture/artifact/typed locator и expected text. Optional `request_id` закрепляет operation; без него выбирается последняя подходящая запись по `(created_at, extraction_id)` и сообщается multiple-version limitation.
3. Нормализация версионируется как `nfkc-casefold-whitespace/v1`. Exact/CER/WER/empty рассчитываются только для evaluated samples. Explicit failed берётся из последних supplied batch outcomes; отсутствие record без такого outcome остаётся missing. Oversized text не скорится.
4. `evidence-pack-verify` проверяет сохранённый pack против canonical packets/records и optional artifact bytes. Schema/reference/provenance/hash integrity оформляется отдельным `evidence-pack-integrity/v1`, без пересчёта ranking и без semantic verification.
5. Reports неизменяемы и не принадлежат human-owned knowledge plane. Persistent OCR scheduler по-прежнему не вводится.

## Последствия

- Resume runs можно сопоставлять без перезаписи истории; evaluator использует реальные failures, а не догадки.
- Можно сравнивать extractor versions одним pinned sample, но CER/WER остаются text-similarity metrics и требуют ручной проверки смысловых ошибок.
- Tampered/dangling refs и corrupt bytes обнаруживаются до использования pack; истинность source при этом не доказывается.
- 1000-item fake-tool smoke проверяет runner/report overhead и resume, но не живой OCR throughput, OCR quality или crash-safe distributed scheduling.

## Gate

На реальном golden set зафиксировать exact/CER/WER, empty/failed/missing, смысловые ошибки и review time по extractor version. Для representative packs проверить working locators/artifacts и результаты byte audit. После этого решать, нужны ли ASR/keyframes или изменения OCR pipeline.
