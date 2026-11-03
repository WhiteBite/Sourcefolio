# ADR 0005: Bounded async ingestion и resumable control state

- Статус: accepted
- Дата: 2026-11-03

## Контекст

Backlog содержит 1000+ элементов. Нельзя одновременно запускать 1000 downloads, держать весь export и медиа в памяти или после сбоя начинать всю партию заново. При этом async не ускоряет CPU-heavy OCR/ASR и не отменяет ограничения filesystem/SQLite.

## Решение

- Фиксированный async worker pool и queue с backpressure.
- Network/subprocess adapters — async; файловый hash/archive — `asyncio.to_thread`.
- CPU-heavy extraction позже выносится в bounded process workers, не в thread pool.
- Повторы только для явно `retryable` ошибок; exponential backoff + jitter.
- `task_id` отвечает за логический source; `operation_hash = hash(task input + adapter operation key)` отвечает за конкретную обработку.
- SQLite control plane хранит jobs и attempts отдельно от evidence archive и Obsidian.
- Только succeeded operation пропускается при resume. Partial/failed можно повторить; новая adapter operation не наследует skip старой.
- Полнота source packet (`complete/partial`) не смешивается с успехом операции (`succeeded/partial/failed`). Metadata-only import может успешно завершиться и оставить честный partial packet.
- Подготовленный operation hash вычисляется один раз и переиспользуется в queue, jobs и attempts.

## Ограничения

Один активный batch runner на SQLite control DB. Конкурентные процессы потребуют owner leases и heartbeat; пока они не вводятся без необходимости. Короткие SQLite transactions выполняются на event-loop thread, а длительная файловая работа выносится в threads.

## Последствия

Память и число одновременных операций ограничены, crash recovery становится проверяемым, а media adapter можно добавить без изменения домена или архива. Производительность проверяется concurrency/retry/resume тестами и последующими замерами на реальных данных, а не предполагается.
