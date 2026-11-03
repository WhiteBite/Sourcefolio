# Evidence Vault

Рабочее название локальной системы, которая превращает сохранённые TikTok-видео, слайдшоу и другие источники не в очередную базу саммари, а в **архив доказательств, личный контекст и историю решений**.

> Реализованы evidence-ядро, resumable ingestion, `yt-dlp` video path, отдельный `gallery-dl` slideshow resolver, archive audit, operational capture report, bounded OCR backlog + saved reports, OCR quality evaluation, generated Obsidian cards/overview, lexical search + evaluation, retrieval-only evidence packs + integrity verification и versioned OCR records. Дополнительно выполнены live public surrogate ingest (1 video + 1 slideshow) и real Tesseract на 6 synthetic golden slides. Это не заменяет пилот личного export и human-reviewed качество. ASR/keyframes, LLM answers и wiki-compilers остаются следующими этапами.

## Главная идея

**Archive everything, extract cheaply, compile selectively, research on demand, rebuild syntheses, verify through outcomes.**

- Сохранённое видео — не истина и не обязательство его переработать.
- Raw capture доказывает только то, что источник содержал определённые данные.
- `creator_claim`, `agent_inference`, `external_observation` и `user_outcome` не смешиваются.
- Backlog — candidate pool. Синтез создаётся вокруг реального вопроса, проекта или решения.
- Obsidian — человекочитаемый vault и интерфейс, не очередь задач и не база состояния обработки.
- Загрузчики, модели, поиск и wiki-движки заменяемы. Пользователю принадлежат ID, архив, контракты, ручные связи, контекст, решения и outcomes.

## Что уже работает

1. `SourcePacket` со стабильным `source_id` и content-derived `capture_id`.
2. Потоковое сохранение больших файлов без загрузки всего файла в память.
3. Content-addressed артефакты `art_<sha256>` и защита от перезаписи.
4. Идемпотентный повтор одинакового capture; новая версия capture не затирает прежнюю.
5. Строгие evidence-контракты без универсального «trust score».
6. JSON Schema экспортируется из кода для сменных реализаций.
7. Потоковый TikTok JSON/TXT export parser с сохранением raw entries и дедупликацией.
8. Bounded async worker pool, backpressure, retry с backoff/jitter и thread-offloaded archive.
9. Resumable SQLite jobs/attempts; успешные операции пропускаются, partial/failed повторяются.
10. `yt-dlp` subprocess adapter: site limit, timeouts, process kill, byte/disk limits и sanitized metadata.
11. Dedicated `gallery-dl` slideshow resolver: ordered images, inventory/completeness checks, separate operation key и sanitized metadata.
12. Read-only `archive-audit`: наличие/SHA-256 артефактов и orphan report без автоматического удаления.
13. Generated Obsidian source cards и overview: YAML properties, provenance, limitations, OCR/ASR excerpts и artifact links; human notes не перезаписываются.
14. Lexical search по packets, textual artifacts и derived OCR text; filters и воспроизводимый ranking.
15. `search-evaluate`: judged queries, hit rate, MRR, recall и precision@k.
16. `ExtractionRecord v1`: typed slide/timestamp/text-span locators, request/cache IDs, extractor version/config и immutable derived storage.
17. `ocr-capture` и `ocr-backlog`: bounded Tesseract subprocesses, filters, latest-per-source, limits, failure isolation, idempotent resume и p50/p95 metrics.
18. `capture-report`: read-only operational JSON/Markdown из SQLite jobs/attempts, retries, durations, stale candidates и sanitized recent errors.
19. `evidence-pack`: query-driven JSON/Markdown с ranked sources, snippets, typed locators, artifact links, provenance и limitations — без автоматического ответа.
20. `ocr-backlog --report`: immutable `ocr-backlog-report/v1` с parameters, extractor config, per-item request IDs, failures и resume history.
21. `ocr-evaluate`: golden `ocr-evaluation/v1`, NFKC/casefold/whitespace normalization, exact/CER/WER, empty/failed/missing и per-extractor metrics.
22. `evidence-pack-verify`: schema/reference/provenance checks и optional SHA-256 verification сохранённого pack.

## Быстрый старт

Требуется Python 3.12+.

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
pytest
ruff check .
ruff format --check .
mypy src
```

Импортировать synthetic-пример:

```bash
evidence-vault archive-capture \
  examples/manual-capture/manifest.json \
  --archive-dir data/archive
```

Повтор команды вернёт `created: false` и те же идентификаторы. Локальные данные в `data/` не входят в репозиторий.

Проверить TikTok export без записи, затем архивировать saved-item metadata ограниченным worker pool:

```bash
evidence-vault tiktok-inspect \
  examples/tiktok-export/user_data.json \
  --export-timezone Europe/Moscow

evidence-vault tiktok-import \
  examples/tiktok-export/user_data.json \
  --export-timezone Europe/Moscow \
  --concurrency 4
```

`tiktok-import` **не скачивает медиа**: он создаёт resumable backlog и честные partial packets. Повтор той же операции вернёт `skipped` для уже обработанных задач.

Для видео установить optional media dependency и запустить отдельную operation:

```bash
python -m pip install -e '.[media]'
evidence-vault tiktok-download /path/to/user_data.json \
  --concurrency 4 \
  --site-limit 2 \
  --limit 10
```

`--limit 10` подходит для первого пилота. `tiktok-download` по-прежнему не объявляет slideshow решённым. Для слайдшоу используется отдельная operation:

```bash
python -m pip install -e '.[slides]'
evidence-vault tiktok-slides /path/to/user_data.json \
  --concurrency 4 \
  --site-limit 2 \
  --max-slides 50 \
  --limit 10
```

Resolver отбирает только slideshow tasks и считает capture complete лишь при непрерывной нумерации и совпадении числа images с `imagePost.images`. Иначе сохраняет честный partial. Живой TikTok не вызывается в test suite: оба subprocess-пути проверены fake executables.

Пересобрать generated source cards и выполнить baseline search:

```bash
evidence-vault project-obsidian \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault

evidence-vault search "нейронки агенты" \
  --archive-dir data/archive \
  --collection Нейронки \
  --limit 10

evidence-vault evidence-pack "нейронки агенты" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --format markdown \
  --excerpt-limit 3
```

Cards появляются только в `data/vault/_generated/evidence-vault/source-cards/`, а coverage dashboard — в `.../overview/index.md`; ручные заметки следует хранить вне этого machine-owned пути. Search использует AND для терминов, Unicode casefold и bounded textual artifacts. Он не выполняет OCR сам, но подхватывает уже сохранённые derived records и не присваивает источникам trust score. `evidence-pack` лишь собирает проверяемые ссылки и excerpts: relevance не является истинностью, автоматический ответ не создаётся.

После установки Tesseract с нужными language data можно извлечь OCR из слайдов одного capture. ID берётся из результата search/source card:

```bash
evidence-vault ocr-capture cap_<64-hex> \
  --language eng+rus \
  --concurrency 2 \
  --limit 50

evidence-vault search "текст со слайда"
```

OCR пишет только `data/derived/`, не изменяет raw SourcePacket. Повтор с тем же Tesseract version/config пропускается по request ID; смена языка или версии создаёт новую операцию. Для строгого model cache дополнительно укажите `--tessdata-dir /path/to/tessdata`: SHA-256 traineddata войдёт в request. Без него результат сохраняется с limitation `language data fingerprint unavailable`.

Для bounded batch по backlog используйте `ocr-backlog` (по умолчанию только latest capture на source, 10 captures и 100 slides):

```bash
evidence-vault ocr-backlog \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --collection Нейронки \
  --capture-limit 10 \
  --slide-limit 100 \
  --concurrency 2 \
  --report data/reports/ocr-run-001.json

evidence-vault capture-report \
  --control-db data/control/capture.sqlite3 \
  --format markdown
```

`ocr-backlog` изолирует failed slides, повторяет их при следующем запуске и возвращает created/skipped/failed plus p50/p95. Это bounded runner без persistent multiprocess OCR queue. `--report` создаёт immutable JSON; для каждого запуска укажите новый путь, чтобы resume history не перезаписывалась. `capture-report` только читает SQLite и не меняет jobs.

Golden OCR samples используют stable `capture_id + artifact_id + typed locator` и `expected_text`; optional `request_id` закрепляет конкретную extractor operation. ID и request IDs доступны в evidence pack/batch report. Синтетический шаблон лежит в `examples/ocr-evaluation/`; перед запуском замените zero IDs и текст на реальную human-reviewed разметку:

```bash
evidence-vault ocr-evaluate /path/to/ocr-samples.json \
  --derived-dir data/derived \
  --batch-report data/reports/ocr-run-001.json \
  --output data/reports/ocr-evaluation-001.json

evidence-vault evidence-pack "текст со слайда" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  > data/reports/evidence-pack-001.json

evidence-vault evidence-pack-verify data/reports/evidence-pack-001.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --verify-bytes
```

Evaluator возвращает exact/CER/WER и empty rate по evaluated samples; failed ставится только при явной failed-записи в supplied batch reports, иначе остаётся missing. CER/WER измеряют текстовое сходство, не истинность. Integrity exit code 1 означает найденные reference/hash issues; невалидная pack schema отклоняется при загрузке.

Проверить evidence archive и прогнать judged search queries:

```bash
evidence-vault archive-audit --verify-bytes

evidence-vault search-evaluate \
  examples/search-evaluation/queries.json
```

`archive-audit` ничего не удаляет: orphan artifacts только выводятся в report. Example queries synthetic; для настоящего gate нужно записать 10 реальных вопросов и релевантные IDs.

Экспортировать машинный контракт:

```bash
evidence-vault schema source-packet
evidence-vault schema evidence-item
evidence-vault schema capture-task
evidence-vault schema search-evaluation
evidence-vault schema extraction-record
evidence-vault schema evidence-pack
evidence-vault schema evidence-pack-integrity
evidence-vault schema ocr-backlog-report
evidence-vault schema ocr-evaluation
evidence-vault schema ocr-evaluation-report
```

## Структура

```text
src/evidence_vault/
  domain/          # устойчивые контракты и инварианты; нет инфраструктуры
  application/     # use cases и порты; нет конкретных адаптеров
  adapters/        # filesystem, TikTok export + yt-dlp/gallery-dl, SQLite, OCR
  cli.py           # тонкий входной адаптер
contracts/         # экспортированные JSON Schema для внешних реализаций
docs/              # продукт, архитектура, исследование, пилот и ADR
examples/          # только synthetic-примеры
tests/             # домен, вертикальный срез, CLI и architecture fitness
```

## Документы

- [Продуктовая рамка](docs/PRODUCT.md)
- [Архитектура и границы данных](docs/ARCHITECTURE.md)
- [Доменные контракты](docs/DOMAIN.md)
- [Дорожная карта и gates](docs/ROADMAP.md)
- [План сравнительного пилота](docs/PILOT.md)
- [Real Tesseract на synthetic golden slides](docs/pilots/synthetic-ocr/README.md)
- [Public surrogate live TikTok ingest](docs/pilots/public-ingest/README.md)
- [Производительность и ограничители](docs/PERFORMANCE.md)
- [Исследование инструментов](docs/RESEARCH.md)
- [ADR](docs/adr/)

## Следующий шаг

Нужен пилот на реальном export пользователя и representative личном backlog, включая недоступные элементы и interrupted live resume. Public surrogate video/photo уже проверен, но не закрывает collection coverage и failure mix. Real Tesseract на known-text synthetic slides проверил OCR/resume/evaluation/integrity; остаётся разметить human-reviewed реальный OCR golden set и 10 реальных judged questions. ASR/keyframes подключаются через тот же derived contract после отдельных resource/quality gates. До измеренных результатов не добавляем GraphRAG и не запускаем два wiki-compiler в одном vault.
