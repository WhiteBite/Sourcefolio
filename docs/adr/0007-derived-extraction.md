# ADR 0007: Отдельная derived-evidence плоскость и request-addressed extraction

- Дата: 2026-11-03
- Статус: accepted

## Контекст

OCR/ASR нельзя добавлять в immutable raw SourcePacket: это изменило бы capture identity и смешало наблюдаемые байты с версионируемым распознаванием. Но без общего контракта каждый extractor начнёт создавать несовместимые sidecar-файлы и повторно обрабатывать весь backlog.

## Решение

1. Добавить derived-evidence плоскость в `data/derived/`.
2. `ExtractionRequest` описывает операцию: capture/artifact, extraction kind, typed locator, extractor provenance и config. `request_id` — cache/resume key.
3. `ExtractionRecord` содержит request, created_at, text, normalized excerpt, limitations и content-derived `extraction_id`.
4. Перед вычислением request ID adapter обязан resolve'ить tool/model version. Повтор существующего request не запускает инструмент. Для Tesseract можно дополнительно fingerprint'ить traineddata; отсутствие fingerprint записывается как limitation.
5. Derived text не является creator claim, agent inference или независимым подтверждением. Search может индексировать его как текст того же capture.
6. Первый OCR — отдельный Tesseract subprocess с limits/timeout и bounded workers. ASR/keyframes требуют собственных resource/quality gates.

## Последствия

- Raw packets и их IDs не меняются при добавлении или пересборке OCR.
- Смена языка, модели, версии или конфигурации не наследует неправильный cache hit.
- Поиск может находить содержимое слайдов без LLM и без изменения evidence archive.
- Filesystem request lookup пока сканирует records одного capture; при массовом backfill может понадобиться индекс/control-plane scheduler, но не смена доменных IDs.
- Empty OCR сохраняется с limitation, failed OCR не маскируется под успешный record.

## Gate

На реальном golden set измерить OCR precision/recall, empty/failed rates, время исправления, throughput/RSS и cache hit ratio. Плохой OCR не компенсируется поиском или синтезом.
