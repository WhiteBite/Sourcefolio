# Производительность и эксплуатационные ограничения

## Текущие ограничители

| Параметр | Текущее решение |
|---|---|
| Capture workers | фиксированный async pool, по умолчанию 4 в CLI |
| Queue | максимум `2 × concurrency` ожидающих элементов |
| Файловый archive | `asyncio.to_thread`; SHA-256 chunks по 1 MiB |
| Retry | до 3 adapter attempts, exponential backoff, cap 4 s, jitter 20% |
| Resume | `operation_hash`, SQLite jobs/attempts, succeeded-only skip |
| SQLite | WAL, `synchronous=NORMAL`, busy timeout 5 s |
| Export | streaming JSON events и построчный TXT; весь исходный export не загружается как JSON object |
| Подготовка task | input/operation hash вычисляется один раз на постановку |
| Source-card rebuild | staging directory + замена только dedicated generated namespace |
| Lexical search | AND terms, Unicode casefold, title/collection weighting; deterministic tie-break |
| Text indexing | максимум 1 MiB на textual artifact/derived record по умолчанию; binary video не читается |
| Slideshow resolver | site limit, max slides, per-slide/total bytes, metadata limit, timeouts/kill |
| OCR workers | bounded Tesseract subprocesses, по умолчанию 2; chunks по `2 × concurrency` |
| OCR backlog | latest-per-source по умолчанию; capture/slide limits, filters, failure isolation, wall + item p50/p95 |
| Extraction resume | request ID до запуска; одинаковый extractor/config/locator не выполняется повторно |
| OCR batch reports | один immutable JSON на запуск; per-item IDs/outcomes, без overwrite resume history |
| OCR evaluation | по умолчанию максимум 20 000 normalized chars/sample; oversized не скорится; Levenshtein O(n×m) time |
| Capture report | read-only SQLite projection; finished-attempt duration p50/p95, retries, stale candidates |
| Evidence pack verify | reference/provenance checks; full artifact SHA-256 только при `--verify-bytes` |
| Archive audit | read-only; full SHA-256 только при `--verify-bytes` |

Нет неограниченного `asyncio.gather` по всему backlog и нет загрузки видео целиком в RAM.

## Media adapter: реализовано и оставшиеся gates

`yt-dlp` adapter уже имеет total/socket timeouts, process-group kill, site concurrency limit, subprocess без shell, byte/free-disk limits, cleanup, безопасные ошибки и sanitized metadata без HTTP headers, signed media/thumbnail URLs и cookies. Retry classification ограничена явными transient patterns; private/unknown errors не зацикливаются.

Для slideshow отдельный `gallery-dl` adapter дополнительно ограничивает число слайдов, per-slide/total bytes и metadata size. Complete требует подтверждённого inventory и непрерывного порядка; неизвестный inventory остаётся partial.

До массового backfill всё ещё нужны:

- расширение уже успешного public surrogate (1 video + 1 photo post) до representative пользовательского backlog и живых failure cases;
- rate limit при необходимости по результатам пилота, а не агрессивный parallelism по умолчанию;
- метрики bytes/s, p50/p95 duration, retries, process status и failure reasons;
- проверка open FD/RSS на 1000+ элементах и interrupted-run recovery на реальных файлах.

Live TikTok не вызывается в CI: subprocess и весь archive/resume путь проверяются fake executable.

## Projection и search baseline

Source-card renderer потоково принимает packets и записывает generated namespace через staging; overview дополнительно материализует packet summaries для сортировки/агрегации. Search и evidence-pack пересобирают in-memory index при каждом запуске: чтение packets потоковое, но documents затем материализуются. Лимит textual artifact (по умолчанию 1 MiB) ограничивает каждый файл, **не общий RSS**. Это осознанный baseline для примерно 1000 saved items, а не обещание масштабирования на неограниченный архив.

Если замеры покажут неприемлемые latency/RSS или lexical quality окажется недостаточным, следующим шагом будет persistent FTS/hybrid adapter за тем же портом; source IDs и evidence archive менять не потребуется.

## Derived OCR

`ocr-capture` ограничивает images одного capture; `ocr-backlog` дополнительно ограничивает число captures и общее число slides, по умолчанию выбирает latest capture на source и поддерживает filters. Каждый worker только запускает отдельный Tesseract subprocess; Python threads не выполняют распознавание сами. Input читается chunks с верхним limit, output пишется во временный файл и проверяется после завершения; timeout и free-disk check ограничивают аварийные случаи. Ошибка одного slide изолируется, успешные request IDs пропускаются при resume, failed slides повторяются. Report содержит created/skipped/failed, wall time и item p50/p95.

Backlog selection читает packet index и не создаёт неограниченный subprocess pool, но это ещё не persistent queue: нет OCR jobs в SQLite, distributed leases или глобального RSS limit. Для массового backfill нужны замеры реального throughput/RSS и отдельный scheduler только при доказанной необходимости.

`--report` сохраняет полный per-item report, поэтому при 1000+ items размер JSON и время сериализации учитываются в wall time. `ocr-evaluate` загружает records только для requested capture IDs; golden expected/actual normalized text ограничен `max_text_chars`, но Levenshtein остаётся O(n×m) по времени и большие golden documents следует разбивать на фрагменты. Simulated `KeyboardInterrupt` test подтверждает request-level resume после завершения worker pool; это не доказательство crash-safe distributed scheduling или SIGKILL cleanup.

## Тесты, уже проверяющие инварианты

- Реальный parallelism с верхним ограничением `max_active <= concurrency`.
- Нет `read()` без bounded chunk для artifact streams.
- Retryable ошибки повторяются ограниченное число раз; history attempts сохраняется.
- Непretryable/unknown ошибка не зацикливается и не отменяет остальную партию.
- Resume пропускает succeeded, но повторяет partial и не путает разные adapter operations.
- Crash recovery переводит stale running job/attempt в обрабатываемое состояние.
- Повторный capture не создаёт дубли; одинаковые bytes дедуплицируются.
- Cleanup вызывается после thread-offloaded archiving.
- Concurrent batch сохраняет порядок результатов относительно входных tasks.
- Generated rebuild удаляет stale documents только в собственном namespace и запрещает path escape.
- Search поддерживает Unicode casefold, AND-запросы, filters/ranking и не читает oversized text artifacts.
- Slideshow resolver проверяет order/inventory, sanitized metadata, partial semantics, byte limits и process failures.
- OCR request cache не вызывает executable повторно; смена language создаёт новый request; raw packet не меняется.
- OCR backlog соблюдает limits/latest-per-source, изолирует failures и повторяет только failed slides; p50/p95 рассчитываются общим metrics-модулем.
- Simulated worker interruption сохраняет completed records и повторный runner завершает оставшиеся items без дублей.
- OCR evaluation проверяет exact/CER/WER/empty, request pinning, multiple versions, explicit failed/missing и oversized behavior.
- Capture report агрегирует retries/durations/stale candidates без записи в control DB.
- Evidence pack сохраняет provenance/locators/artifact links и не создаёт answer; verifier обнаруживает tampered/dangling refs и corrupt bytes.
- Archive audit обнаруживает missing/corrupt artifacts и не удаляет orphans.
- Search evaluation проверяет per-query ranks и агрегированные метрики.

## Smoke-замер 2026-11-03

Synthetic metadata-only, 300 уникальных TikTok entries, 8 workers, локальный sandbox, без сети и реальных видео:

- первый import: **0.749 s**, 300 succeeded;
- повторный resume: **0.314 s**, 300 skipped;
- ровно 300 source packets.

Это проверка масштабирования ingestion/control plane, не SLA и не benchmark медиа. Следующие замеры на реальном export должны включать RSS, wall time, SQLite time, число файловых операций и failure mix.

## Smoke-замер baseline A на 1000 items — 2026-11-03

Synthetic TXT (`Link: ...`), 1000 уникальных TikTok video IDs, 8 workers, локальный sandbox, без сети и медиа. Peak RSS измерен через `resource.getrusage(RUSAGE_CHILDREN)` для отдельного CLI process:

| Operation | Wall time | Peak RSS | Результат |
|---|---:|---:|---|
| Metadata import | 1.255 s | 46 060 KiB | 1000 succeeded |
| Повторный resume | 0.345 s | 44 164 KiB | 1000 skipped |
| Source-card rebuild | 0.972 s | 40 948 KiB | 1000 cards |
| Index build + один search | 0.291 s | 41 516 KiB | 1 точное совпадение |

На диске осталось ровно 1000 packets и 1000 cards. Это подтверждает базовую управляемость synthetic backlog, но не качество поиска, не поведение больших captions/OCR и не live media throughput.

## Сквозной derived smoke — 2026-11-03

Fake `gallery-dl` + fake Tesseract, два слайда, два OCR workers: slideshow complete, первый OCR создал 2 records, повторный вернул 2 skipped, derived search нашёл capture, Obsidian rebuild создал card, judged evaluation hit rate 1.0, full artifact audit прошёл. SHA-256 raw packet до и после OCR совпал. Это проверка интеграции и resume, не benchmark и не live validation.

Расширенный operational/retrieval smoke в том же sandbox: 3 synthetic TikTok jobs + 1 slideshow; `ocr-backlog` создал 2 records и при повторе вернул 2 skipped; `capture-report` прочитал 3 jobs/3 attempts; `evidence-pack` вернул 1 source с 2 typed excerpts; Obsidian rebuild создал 4 cards + overview; derived search нашёл 1 result, judged evaluation hit rate 1.0, full audit OK. Все 4 raw packets остались byte-identical. Живые tools не использовались; timing двух fake OCR items не является производительным результатом.

## OCR backlog scale/report smoke — 2026-11-03

100 synthetic captures × 10 slides, 1000 unique small image artifacts, fake `/bin/sh` Tesseract, 4 workers, полный CLI + 1000 extraction records + immutable report на каждый запуск:

| Operation | Wall time | Peak RSS | Результат |
|---|---:|---:|---|
| Первый backlog | 2.680 s | 51 728 KiB | 1000 created, 0 failed |
| Resume + второй report | 3.021 s | 51 876 KiB | 1000 skipped, 0 failed |

Оба `ocr-backlog-report/v1` содержат ровно 1000 items и проходят contract validation; все raw packets byte-identical. Первый item p50/p95 — 0.00717/0.01380 s только для fake subprocess. Resume wall включает чтение 1000 records и сериализацию второго report, поэтому эти цифры не являются живым OCR throughput или утверждением, что resume должен быть быстрее первого запуска.

## Real-tool surrogate checks — 2026-11-03

[Public live ingest](pilots/public-ingest/README.md): настоящие `yt-dlp 2026.10.26` и `gallery-dl 1.32.19`, по одному public video/photo post, concurrency/site limit 1 и max attempts 1. Video complete (1 оригинальный video artifact, 2.754 s), slideshow complete (3 confirmed ordered images, 1.894 s). Повторы дали по 1 skipped за 0.227/0.245 s; raw packets и фактические artifact bytes byte-identical, full audit OK. Это point-in-time network smoke, не p95 или массовый throughput.

[Real Tesseract на synthetic golden](pilots/synthetic-ocr/README.md): 6 PNG, Tesseract 5.5.0, `eng+rus`, 2 workers. Первый run 6 created за 1.156 s, resume 6 skipped за 0.299 s; request IDs и derived bytes совпали. Evaluation с двумя immutable reports: 5/6 exact, mean CER 0.0042735, 0 failed/missing; pack integrity проверил 6 artifacts. Маленький synthetic run не заменяет real-user OCR quality или throughput/RSS gate.

## Следующие performance gates

1. 1000+ реальных saved items: wall time, peak RSS, open FD count и resume after interruption.
2. Отдельно видео и slideshow: throughput, p95, bytes, partial rate и повторные requests на representative backlog; единичный public smoke выше не закрывает этот gate.
3. Массовый реальный OCR, затем ASR: measured throughput/RSS, cache hit ratio и отсутствие повторной обработки неизменного capture; 6-slide real-Tesseract smoke уже подтвердил cache/resume, но не масштаб. Model reuse обязателен для будущего in-process ASR.
4. Поиск/wiki: сравнивать latency и качество с baseline A до добавления vector/GraphRAG инфраструктуры.
