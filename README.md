# Evidence Vault

Рабочее название локальной системы, которая превращает сохранённые TikTok-видео, слайдшоу и другие источники не в очередную базу саммари, а в **архив доказательств, личный контекст и историю решений**.

> Реализованы evidence-ядро, resumable ingestion, `yt-dlp` video path, отдельный `gallery-dl` slideshow resolver, archive audit, operational capture report, bounded OCR backlog + saved reports, OCR quality evaluation, generated Obsidian cards/overview, lexical search + evaluation, retrieval-only evidence packs + integrity verification и versioned OCR records. Добавлены TikTok-collections pipeline (userscript → `import-collections` → collection indexes, triage dashboards и human `ideas/`), per-collection LLM digest (`llm-enrich`) и MCP-сервер. Дополнительно выполнены live public surrogate ingest (1 video + 1 slideshow) и real Tesseract на 6 synthetic golden slides. Это не заменяет пилот личного export и human-reviewed качество. ASR/keyframes и wiki-compilers остаются следующими этапами.

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
23. `import-collections`: архивирует `collection-capture/v1` metadata-only packets с resumable bounded workers; повтор идемпотентен.
24. `project-obsidian` collection indexes: per-collection `collection-index/v1` со списком видео и извлечёнными repositories (GitHub/GitLab) и hashtags.
25. Triage dashboards (`triage-dashboard/v1`, `inbox`/`keep`/`drop`/`done`) на Dataview и human `ideas/` scaffolding без перезаписи.
26. `llm-enrich`: per-collection `llm-digest/v1` через OpenAI-compatible chat model, cache и явная пометка `agent_inference`.
27. MCP-сервер `evidence-vault-mcp`: CLI как MCP tools для search, evidence packs, collections, Obsidian projection и operational reports.
28. `project-fit`: сопоставление latest captures с проектами пользователя через chat model, накопительный индекс совпадений и per-project briefs `project-fit/v1`.
29. Knowledge-base surfaces: human `ideas/`/`projects/`/`decisions/`, generated `entities/` (repo nodes), `graph/` (Mermaid), `decisions/` index и `projects/` briefs; команды `scaffold-projects`, `decision-new`, `decision-search`, `context` и `project-fit --profiles`.
30. `ask`: единая точка входа — retrieval через search + evidence-pack и optional grounded LLM synthesis только по excerpts; immutable per-run note `answer/v1` в `answers/` с `provenance: agent_inference`, citations и human decision stub.
31. `profile-draft`: черновик human-профиля проекта из его собственного репозитория (README + `pyproject.toml`/`package.json`) с `create_if_absent` и без перезаписи.
32. `refresh`: последовательный прогон `import-collections` (при `--capture`) → `project-obsidian` → `project-fit` (при `--fit`) с одним combined JSON; без daemon, расписание ручное.

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
evidence-vault schema collection-capture
```

## TikTok collections → база знаний

Tampermonkey userscript `userscripts/tiktok-collections-capture.user.js` собирает коллекции из залогиненной веб-сессии TikTok в `collection-capture/v1` JSON (или CSV). Режим «Собрать» делает полный слепок, «Только новое» — инкрементальный diff против сохранённого baseline. Опционально собираются топ-20 комментариев по выбранным коллекциям. Прогресс хранится в GM storage: пауза, стоп и продолжение с места остановки.

Архивировать metadata-only packets (медиа не скачивается):

```bash
evidence-vault import-collections \
  /path/to/tiktok-collections-<stamp>.json \
  --archive-dir data/archive \
  --control-db data/control/capture.sqlite3 \
  --concurrency 4
```

`import-collections` создаёт resumable backlog и честные partial packets; повтор той же операции идемпотентен. Затем пересобрать generated views и, при желании, создать human `ideas/`:

```bash
evidence-vault project-obsidian \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --scaffold-ideas

evidence-vault scaffold-ideas \
  --archive-dir data/archive \
  --vault data/vault
```

`project-obsidian` дополнительно строит per-collection indexes в `<vault>/_generated/evidence-vault/collections/`: для каждой коллекции `<slug>.md` со списком видео и извлечёнными repositories (GitHub/GitLab) и hashtags, плюс `index.md`. Triage dashboards лежат в `.../dashboards/` (`inbox`, `keep`, `drop`, `done`) и используют Dataview-запросы над `ideas/`, поэтому **нужен плагин Obsidian Dataview**. `--scaffold-ideas` создаёт отсутствующие human notes в `<vault>/ideas/<source_id>.md` (frontmatter `status: inbox`) и не перезаписывает существующие; `scaffold-ideas` делает то же без пересборки generated views. `project-obsidian` также пересобирает `entities/`, `graph/` и `decisions/` (см. раздел «База знаний: human и generated зоны»).

Per-collection LLM digest:

```bash
evidence-vault llm-enrich \
  --collection Нейронки \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --limit 20
```

`llm-enrich` прогоняет latest captures коллекции через OpenAI-compatible chat model и пишет `llm-digest/v1` в `<vault>/_generated/evidence-vault/llm/<slug>.md`. Digest помечен `provenance: agent_inference` и warning-callout: это machine inference, не `creator_claim`. Конфигурация резолвится по приоритету: флаги `--base-url`/`--api-key`/`--model`, затем env `EV_LLM_BASE_URL`/`EV_LLM_API_KEY`/`EV_LLM_MODEL`, затем `data/llm.json` (ключи `base_url`/`api_key`/`model`), затем встроенные defaults. Ответы кэшируются в `data/derived/`; `--refresh` игнорирует кэш, `--dry-run` ничего не пишет.

Каталог репозиториев коллекции:

```bash
evidence-vault repo-catalog \
  --collection Нейронки \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`repo-catalog` извлекает ссылки на репозитории из latest captures коллекции (desc + комментарии), дедуплицирует по нормализованному `owner/repo` и обогащает GitHub-репозитории через REST API (`GET https://api.github.com/repos/{owner}/{repo}`): stars, language, license, archived, last push, topics. Результат — note `repo-catalog/v1` в `<vault>/_generated/evidence-vault/repos/<slug>.md` с `provenance: external_observation`: таблица репозиториев плюс число видео и wikilinks на упомянувшие их source cards. GitLab-ссылки подсчитываются, но не обогащаются (v1 — только GitHub). Токен берётся из `--token` или env `GITHUB_TOKEN`; без токена лимит API — 60 запросов/час, а 403/429/404 изолируются как ошибка отдельного репозитория, прогон продолжается. Метаданные кэшируются в `data/derived/repos/`; `--refresh` игнорирует кэш, `--dry-run` ничего не пишет и не запрашивает.

Сопоставление сохранённого с проектами:

```bash
evidence-vault project-fit \
  --collection Нейронки \
  --projects data/projects.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`project-fit` прогоняет latest captures коллекции через OpenAI-compatible chat model и сопоставляет каждый элемент с проектами из `--projects` (JSON `{"projects": [{"id", "name", "description"}]}`). Результат — per-project briefs `project-fit/v1` в `<vault>/_generated/evidence-vault/projects/<slug>.md` плюс `projects/index.md` со списком проектов и числом глобально нерелевантных элементов. Каждый brief помечен `provenance: agent_inference` и warning-callout: это machine inference, не `creator_claim`.

`--collection` можно повторять; без него сканируется latest capture каждого источника в архиве (это может быть много). Совпадения накапливаются в `<derived-dir>/project-fit/index.json`: повторный запуск с другой коллекцией не затирает прежние briefs, а пересобирает все заметки из индекса. `--refresh` перезапрашивает модель и перезаписывает записи обработанных элементов; без него ответы берутся из кэша. `--max-ocr-kb` (по умолчанию 20) ограничивает объединяемый OCR-текст на элемент. `--dry-run` печатает план и первые промпты в stderr, ничего не записывая. Конфигурация модели резолвится так же, как у `llm-enrich`.

## База знаний: human и generated зоны

Vault делится на две зоны. Human-зона хранит канонические заметки, которые пишет пользователь. Generated-зона пересобирается командами и никогда не перезаписывает human-заметки.

Human-зона:

- `<vault>/ideas/`: заметки по сохранённым источникам (`<source_id>.md`, frontmatter `status: inbox`). Создаются `scaffold-ideas` или `project-obsidian --scaffold-ideas`.
- `<vault>/projects/`: профили проектов (`<project.id>.md`, frontmatter `type: project`, `status: draft`). Создаются `scaffold-projects`.
- `<vault>/decisions/`: решения (`<date>-<slug>.md`, frontmatter `type: decision`, `status: open`, `projects`, `tags`). Создаются `decision-new`.

Generated-зона `<vault>/_generated/evidence-vault/`:

- `source-cards/`, `overview/`, `collections/`, `dashboards/`, `llm/`, `repos/`: как описано выше.
- `entities/`: по одной заметке на GitHub-репозиторий (`<owner>__<repo>.md`, `type: repo`, `provenance: external_observation`). Метаданные берутся из кэша `repo-catalog`, плюс число упомянувших источников и связанные проекты.
- `graph/index.md`: knowledge graph на Mermaid (`type: graph`) с проектами, идеями, коллекциями и репозиториями. Строится из всех последних захватов (project→collection→idea→repo), а не только из совпадений `project-fit`; рёбра project→idea упорядочены по релевантности и ограничены; frontmatter сообщает `idea_shown` / `idea_total` / `idea_truncated`. Рендерится без плагинов.
- `decisions/index.md`: индекс human-решений (`decision-index/v1`) со ссылками на заметки в `decisions/`.
- `projects/`: per-project briefs `project-fit/v1` и `index.md`.

`project-obsidian` пересобирает source cards, overview, collection indexes, dashboards, а также `entities/`, `graph/` и `decisions/`. Mermaid-граф рендерится без плагинов; triage dashboards по-прежнему требуют плагин Dataview.

Команды:

```bash
evidence-vault scaffold-projects \
  --vault data/vault \
  --projects data/projects.json

evidence-vault decision-new \
  --vault data/vault \
  --title "Выбрать gallery-dl для слайдшоу" \
  --problem "Нужен надёжный resolver для photo posts" \
  --option "yt-dlp" \
  --option "gallery-dl" \
  --chosen "gallery-dl" \
  --rationale "Отдельный slideshow path с inventory checks" \
  --project evidence-vault \
  --tag ingestion

evidence-vault decision-search "gallery-dl" --vault data/vault

evidence-vault context \
  --vault data/vault \
  --derived-dir data/derived \
  --archive-dir data/archive \
  --projects data/projects.json \
  --format markdown

evidence-vault project-fit \
  --collection Нейронки \
  --projects data/projects.json \
  --profiles data/vault/projects \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`scaffold-projects` создаёт отсутствующие профили в `<vault>/projects/<project.id>.md` и не перезаписывает существующие. `decision-new` создаёт заметку решения в `<vault>/decisions/` (тоже без перезаписи); `decision-search` ищет по терминам (AND, casefold) и возвращает path/title/date/status. `context` собирает session brief из проектов, накопленных совпадений `project-fit` и решений; `--format markdown` печатает человекочитаемый вид, по умолчанию JSON, и дополнительно пишет бриф в `<vault>/_generated/evidence-vault/context/index.md` (+ `context.json`). Отсутствие файла `--projects` не роняет команду. `project-fit --profiles <dir>` подмешивает в промпт тело профиля `<project.id>.md` (без frontmatter) из указанной директории. `id` проекта обязан соответствовать `^[a-z0-9][a-z0-9-]*$`.

## Ответы, профили и refresh

`ask` — единая точка входа: retrieval через search + evidence-pack и, если доступна модель, grounded synthesis только по найденным excerpts. Без ключа или с `--no-llm` команда работает в retrieval-only режиме:

```bash
evidence-vault ask "какие подходы к RAG описаны в сохранённом" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --format markdown

evidence-vault ask "какие подходы к RAG описаны в сохранённом" \
  --no-llm \
  --limit 10 \
  --excerpt-limit 3
```

`--project <id>` подмешивает имя, описание и профиль проекта в промпт и помечает заметку, но не меняет ranking retrieval. Каждый запуск пишет новую immutable заметку `answer/v1` в `<vault>/_generated/evidence-vault/answers/<UTC-stamp>-<slug>.md` (microsecond-stamped имя, `provenance: agent_inference`, warning-callout). Заметка пишется через `write_immutable_bytes`, а не через `replace_namespace`, поэтому `project-obsidian` её никогда не затирает. В заметке и в JSON-выводе есть copy-pasteable stub `evidence-vault decision-new ...`; human-решение командой не создаётся автоматически. `status: degraded` (модель недоступна) возвращает exit code 1 и оставляет retrieval excerpts.

`profile-draft` черновик human-профиля проекта из его собственного репозитория (README + `pyproject.toml`/`package.json`: name/description/dependency names/keywords). Один проект:

```bash
evidence-vault profile-draft \
  --repo /path/to/repo \
  --id my-project \
  --vault data/vault
```

Batch по всем проектам, у которых в `projects.json` задан `repo_path`:

```bash
evidence-vault profile-draft \
  --projects data/projects.json \
  --vault data/vault \
  --limit 10
```

Заметка пишется в `<vault>/projects/<id>.md` через `create_if_absent` и никогда не перезаписывает human-заметку. JSON-вывод: `{created, skipped, created_paths, projects_root, errors}`; нечитаемый репозиторий даёт exit code 1.

`refresh` — одна команда, которая последовательно переиспользует существующие handlers: `import-collections` (только при `--capture <file>`) → `project-obsidian` (всегда) → `project-fit` (только при `--fit`). stdout каждого шага захватывается, печатается один combined JSON `{"steps": [{"step", "exit", "output"}, ...]}`; шаг с ошибкой даёт `{"step", "error"}`. Exit code ненулевой, если упал любой шаг.

```bash
evidence-vault refresh \
  --capture /path/to/tiktok-collections-<stamp>.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault

evidence-vault refresh \
  --fit \
  --collection Нейронки \
  --projects data/projects.json \
  --vault data/vault
```

Daemon/watcher нет: расписание ручное — запускайте команду вручную или из планировщика с новым capture-файлом.

## MCP-сервер

Установить extra и запустить сервер:

```bash
python -m pip install -e ".[mcp]"
evidence-vault-mcp
```

Сервер отдаёт CLI как MCP tools: `search`, `evidence_pack`, `import_collections`, `fetch_slides`, `project_obsidian`, `scaffold_ideas`, `llm_enrich`, `llm_catalog`, `repo_catalog`, `project_fit`, `archive_audit`, `capture_report`, `schema`, `context`, `scaffold_projects`, `decision_new`, `decision_search`. Каждый tool принимает те же параметры, что и соответствующая команда, и возвращает её stdout или `error (exit N)`. Инструменты помечены read/write (`read_only_hint`, `destructive_hint`, `open_world_hint`); `decision_new` требует непустой `title`; список проектов передаётся параметром `project_ids`.

Пример регистрации в `opencode.json` (локальный сервер, machine-specific путь к venv; файл gitignored):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "evidence-vault": {
      "type": "local",
      "command": [
        "D:/Sources/WhiteBite/Sourcefolio/.venv/Scripts/python.exe",
        "-m",
        "evidence_vault.mcp_server"
      ],
      "enabled": true
    }
  }
}
```

После правки `opencode.json` нужно перезапустить opencode, чтобы он подхватил сервер.

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

## Кому это нужно / Who is it for

- Для тех, кто сохраняет много коротких видео и слайдшоу и хочет архив доказательств, а не свалку саммари.
- Для исследователей и knowledge workers, которым нужны провенанс, типизированные локаторы и проверяемая выдача.
- Для разработчиков, которым нужен local-first, contract-driven pipeline с возможностью добавить собственные extractor'ы.

## Сценарии использования / Use cases

- Архивировать сохранённые TikTok-видео, слайдшоу и коллекции как неизменяемые content-addressed captures.
- Извлекать OCR-текст из слайдов и искать по нему лексически с провенансом.
- Собирать evidence packs по вопросу с ранжированными источниками, сниппетами и типизированными локаторами.
- Генерировать Obsidian vault с карточками источников, индексами коллекций и triage-дашбордами.
- Сопоставлять сохранённое с собственными проектами и вести историю решений.

## Почему выбрать это / Why choose this

- Неизменяемые content-addressed captures со стабильным `source_id` и content-derived `capture_id`.
- Строгие evidence-контракты: `creator_claim`, `agent_inference`, `external_observation` и `user_outcome` не смешиваются.
- Local-first: архив, ID, контракты и решения принадлежат пользователю.

## Статус / Status

Ядро evidence, resumable ingestion, OCR-пайплайн, лексический поиск и его оценка, evidence packs с проверкой целостности, TikTok-collections pipeline, Obsidian-проекция, LLM-digest и MCP-сервер реализованы и покрыты тестами. Открытые этапы — пилот на реальном export, human-reviewed OCR golden set и ASR/keyframes — перечислены в [дорожной карте](docs/ROADMAP.md).

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

Нужен пилот на реальном export пользователя и representative личном backlog, включая недоступные элементы и interrupted live resume. Public surrogate video/photo уже проверен, но не закрывает collection coverage и failure mix. TikTok-collections pipeline и LLM digest работают на synthetic/локальных данных; остаётся прогнать их на реальном личном export и collections и оценить качество digest и triage. Real Tesseract на known-text synthetic slides проверил OCR/resume/evaluation/integrity; остаётся разметить human-reviewed реальный OCR golden set и 10 реальных judged questions. ASR/keyframes подключаются через тот же derived contract после отдельных resource/quality gates. До измеренных результатов не добавляем GraphRAG и не запускаем два wiki-compiler в одном vault.
