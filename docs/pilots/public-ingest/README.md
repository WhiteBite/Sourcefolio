# Public surrogate live TikTok ingest

Дата проверки: **2026-11-03**. Через настоящие network tools проверены один public video и один public photo post. Входные JSON были небольшими surrogate `FavoriteVideoList` wrappers с фиксированным timestamp — **личный TikTok export пользователя не использовался**.

Cookies, login и bypass не применялись. Скачанные public media и signed CDN URLs не включены в документацию; сохранены sanitized operational summaries, capture reports и SHA-256 проверки.

## Источники и версии

- Video: `https://www.tiktok.com/@.doris_official/video/7488781024571280662`, `yt-dlp 2026.10.26`;
- Photo: `https://www.tiktok.com/@velocitydrawing/photo/7266832970353233185`, `gallery-dl 1.32.19`.

Оба URL были доступны из sandbox в момент проверки. Public posts могут позже измениться или стать недоступными; это point-in-time результат, не постоянный CI test.

## Ограничения запуска

Для каждой операции: `limit=1`, `concurrency=1`, `site-limit=1`, `max-attempts=1`, timeout 120 s, socket timeout 20 s. Video использовал `format-selector=worst` и `max-file-mb=64`; slideshow — `max-slides=10`, `max-slide-mb=10`, `max-total-mb=50`.

## Результат verification pass

| Operation | Первый run | Packet | Оригинальные media | Wall | Resume | Raw/artifact hashes | Byte audit |
|---|---|---|---|---:|---|---|---|
| `tiktok-download` | 1 succeeded, 0 partial/failed | complete, limitations=[] | 1 video, 5 348 655 bytes | 2.754 s | 1 skipped | совпали | OK, 0 issues |
| `tiktok-slides` | 1 succeeded, 0 partial/failed | complete, limitations=[] | 3 images, 331 254 bytes | 1.894 s | 1 skipped | совпали | OK, 0 issues |

Resume занял 0.227 s для video и 0.245 s для photo. В обоих cases `capture-report` показал 1 job / 1 attempt: повтор не запускал новый adapter attempt. SHA-256 raw packet и **фактических bytes каждого artifact** вычислены до и после resume и совпали. `archive-audit --verify-bytes` дополнительно проверил archive после resume.

У photo packet подтверждённый inventory дал 3 ordered `original_image` artifacts; поэтому status `complete`, а не эвристический успех по наличию хотя бы одной картинки. Оба packet также содержат 2 sanitized `author_metadata` artifacts. В summaries оставлены только безопасные metadata keys (`adapter`, `saved_index`, `task_id`), без signed media URLs.

Это единичные измерения на sandbox network, не throughput/SLA. Свежий независимый live run — новое observation и может получить новый `capture_id`; resume внутри одного control/archive run сохраняет исходный packet. `source_id` источника остаётся стабильным.

## Файлы

- `summary.json` — verification pass с per-operation counts, sizes, timings и before/after hashes;
- `summary-first-pass.json` — первый успешный live pass, сохранённый отдельно;
- `video-capture-report.json`, `photo-capture-report.json` — sanitized SQLite operational reports.

## Что остаётся за пределами доказанного

- структура и полнота официального export пользователя, collection coverage и representative личный backlog;
- private/deleted/region-restricted/rate-limited cases и retry classification на живых failures;
- реальное прерывание media subprocess и recovery, массовый backfill, RSS/open-FD и p95;
- качество OCR/ASR на этом public media и полезность evidence-backed answers.

`max-attempts=1` намеренно ограничивал network повторы, поэтому этот run не является live retry test. Живые URL не добавляются в CI: deterministic fake-tool tests остаются отдельным regression слоем.
