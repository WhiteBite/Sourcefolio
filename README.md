# Sourcefolio

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
27. MCP-сервер `sourcefolio-mcp`: CLI как MCP tools для search, evidence packs, collections, Obsidian projection и operational reports.
28. `project-fit`: сопоставление latest captures с проектами пользователя через chat model, накопительный индекс совпадений и per-project briefs `project-fit/v1`.
29. Knowledge-base surfaces: human `ideas/`/`projects/`/`decisions/`, generated `entities/` (repo nodes), `graph/` (Mermaid), `decisions/` index и `projects/` briefs; команды `scaffold-projects`, `decision-new`, `decision-search`, `context` и `project-fit --profiles`.
30. `ask`: единая точка входа — retrieval через search + evidence-pack и optional grounded LLM synthesis только по excerpts; immutable per-run note `answer/v1` в `answers/` с `provenance: agent_inference`, citations и human decision stub.
31. `profile-draft`: черновик human-профиля проекта из его собственного репозитория (README + `pyproject.toml`/`package.json`) с `create_if_absent` и без перезаписи.
32. `refresh`: последовательный прогон `import-collections` (при `--capture`) → `project-obsidian` → `project-fit` (при `--fit`) с одним combined JSON; без daemon, расписание ручное.
33. `serve`: локальный HTTP-сервер архива — отдаёт тот же браузер, что `render-ui`, вместе с превью-артефактами (Range) и записывает triage/заголовок/«зачем сохранил» прямо в human `ideas/` через `POST /api/note`.
34. Реестр кандидатов: `registry-extract`/`registry-build`/`registry-queue`/`candidate-decide` превращают captures в per-project очередь разработок — заякоренные цитаты, трёхсостоянные вердикты, content-addressed append-only snapshots, human-решения с обязательным `approved_by` и browser-вид «Проекты» (`GET /api/registry`, `POST /api/candidate`). Без auto-accept и без межпроектного ранжирования.

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
sourcefolio archive-capture \
  examples/manual-capture/manifest.json \
  --archive-dir data/archive
```

Повтор команды вернёт `created: false` и те же идентификаторы. Локальные данные в `data/` не входят в репозиторий.

Проверить TikTok export без записи, затем архивировать saved-item metadata ограниченным worker pool:

```bash
sourcefolio tiktok-inspect \
  examples/tiktok-export/user_data.json \
  --export-timezone Europe/Moscow

sourcefolio tiktok-import \
  examples/tiktok-export/user_data.json \
  --export-timezone Europe/Moscow \
  --concurrency 4
```

`tiktok-import` **не скачивает медиа**: он создаёт resumable backlog и честные partial packets. Повтор той же операции вернёт `skipped` для уже обработанных задач.

Для видео установить optional media dependency и запустить отдельную operation:

```bash
python -m pip install -e '.[media]'
sourcefolio tiktok-download /path/to/user_data.json \
  --concurrency 4 \
  --site-limit 2 \
  --limit 10
```

`--limit 10` подходит для первого пилота. `tiktok-download` по-прежнему не объявляет slideshow решённым. Вместе с видео архивируется обложка поста (`--write-thumbnail`): она сохраняется как `original_image` и даёт превью в `render-ui`. Для слайдшоу используется отдельная operation:

```bash
python -m pip install -e '.[slides]'
sourcefolio tiktok-slides /path/to/user_data.json \
  --concurrency 4 \
  --site-limit 2 \
  --max-slides 50 \
  --limit 10
```

Resolver отбирает только slideshow tasks и считает capture complete лишь при непрерывной нумерации и совпадении числа images с `imagePost.images`. Иначе сохраняет честный partial. Живой TikTok не вызывается в test suite: оба subprocess-пути проверены fake executables.

Добрать обложки к уже архивированным источникам (нужно для превью в `render-ui`):

```bash
python -m pip install -e '.[media,slides]'
sourcefolio fetch-covers \
  --archive-dir data/archive \
  --collection Нейронки \
  --limit 50
```

`fetch-covers` берёт latest capture каждого источника, скачивает только обложку (`yt-dlp --skip-download --write-thumbnail`, для slideshow — `gallery-dl`) и сохраняет новый capture, который содержит прежние артефакты плюс `original_image`. Исходные captures не меняются. `--collection` повторяется для нескольких коллекций, `--kind` ограничивает типы источников, `--include-existing` перезапускает обработку источников, у которых обложка уже есть. Повторный прогон идемпотентен: источники с обложкой пропускаются. Для `yt-dlp` нужен `curl-cffi` (extra `media`) — без него `--impersonate chrome` не работает.

Пересобрать generated source cards и выполнить baseline search:

```bash
sourcefolio project-obsidian \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault

sourcefolio search "нейронки агенты" \
  --archive-dir data/archive \
  --collection Нейронки \
  --limit 10

sourcefolio evidence-pack "нейронки агенты" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --format markdown \
  --excerpt-limit 3
```

Cards появляются только в `data/vault/_generated/sourcefolio/source-cards/`, а coverage dashboard — в `.../overview/index.md`; ручные заметки следует хранить вне этого machine-owned пути. Search использует AND для терминов, Unicode casefold и bounded textual artifacts. Он не выполняет OCR сам, но подхватывает уже сохранённые derived records и не присваивает источникам trust score. `evidence-pack` лишь собирает проверяемые ссылки и excerpts: relevance не является истинностью, автоматический ответ не создаётся.

После установки Tesseract с нужными language data можно извлечь OCR из слайдов одного capture. ID берётся из результата search/source card:

```bash
sourcefolio ocr-capture cap_<64-hex> \
  --language eng+rus \
  --concurrency 2 \
  --limit 50

sourcefolio search "текст со слайда"
```

OCR пишет только `data/derived/`, не изменяет raw SourcePacket. Повтор с тем же Tesseract version/config пропускается по request ID; смена языка или версии создаёт новую операцию. Для строгого model cache дополнительно укажите `--tessdata-dir /path/to/tessdata`: SHA-256 traineddata войдёт в request. Без него результат сохраняется с limitation `language data fingerprint unavailable`.

Для bounded batch по backlog используйте `ocr-backlog` (по умолчанию только latest capture на source, 10 captures и 100 slides):

```bash
sourcefolio ocr-backlog \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --collection Нейронки \
  --capture-limit 10 \
  --slide-limit 100 \
  --concurrency 2 \
  --report data/reports/ocr-run-001.json

sourcefolio capture-report \
  --control-db data/control/capture.sqlite3 \
  --format markdown
```

`ocr-backlog` изолирует failed slides, повторяет их при следующем запуске и возвращает created/skipped/failed plus p50/p95. Это bounded runner без persistent multiprocess OCR queue. `--report` создаёт immutable JSON; для каждого запуска укажите новый путь, чтобы resume history не перезаписывалась. `capture-report` только читает SQLite и не меняет jobs.

Golden OCR samples используют stable `capture_id + artifact_id + typed locator` и `expected_text`; optional `request_id` закрепляет конкретную extractor operation. ID и request IDs доступны в evidence pack/batch report. Синтетический шаблон лежит в `examples/ocr-evaluation/`; перед запуском замените zero IDs и текст на реальную human-reviewed разметку:

```bash
sourcefolio ocr-evaluate /path/to/ocr-samples.json \
  --derived-dir data/derived \
  --batch-report data/reports/ocr-run-001.json \
  --output data/reports/ocr-evaluation-001.json

sourcefolio evidence-pack "текст со слайда" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  > data/reports/evidence-pack-001.json

sourcefolio evidence-pack-verify data/reports/evidence-pack-001.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --verify-bytes
```

Evaluator возвращает exact/CER/WER и empty rate по evaluated samples; failed ставится только при явной failed-записи в supplied batch reports, иначе остаётся missing. CER/WER измеряют текстовое сходство, не истинность. Integrity exit code 1 означает найденные reference/hash issues; невалидная pack schema отклоняется при загрузке.

Проверить evidence archive и прогнать judged search queries:

```bash
sourcefolio archive-audit --verify-bytes

sourcefolio search-evaluate \
  examples/search-evaluation/queries.json
```

`archive-audit` ничего не удаляет: orphan artifacts только выводятся в report. Example queries synthetic; для настоящего gate нужно записать 10 реальных вопросов и релевантные IDs.

Экспортировать машинный контракт:

```bash
sourcefolio schema source-packet
sourcefolio schema evidence-item
sourcefolio schema capture-task
sourcefolio schema search-evaluation
sourcefolio schema extraction-record
sourcefolio schema evidence-pack
sourcefolio schema evidence-pack-integrity
sourcefolio schema ocr-backlog-report
sourcefolio schema ocr-evaluation
sourcefolio schema ocr-evaluation-report
sourcefolio schema collection-capture
```

## TikTok collections → база знаний

Tampermonkey userscript `userscripts/tiktok-collections-capture.user.js` собирает коллекции из залогиненной веб-сессии TikTok в `collection-capture/v1` JSON (или CSV). Режим «Собрать» делает полный слепок, «Только новое» — инкрементальный diff против сохранённого baseline. Опционально собираются топ-20 комментариев по выбранным коллекциям. Прогресс хранится в GM storage: пауза, стоп и продолжение с места остановки.

Архивировать metadata-only packets (медиа не скачивается):

```bash
sourcefolio import-collections \
  /path/to/tiktok-collections-<stamp>.json \
  --archive-dir data/archive \
  --control-db data/control/capture.sqlite3 \
  --concurrency 4
```

`import-collections` создаёт resumable backlog и честные partial packets; повтор той же операции идемпотентен. Затем пересобрать generated views и, при желании, создать human `ideas/`:

```bash
sourcefolio project-obsidian \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --scaffold-ideas

sourcefolio scaffold-ideas \
  --archive-dir data/archive \
  --vault data/vault
```

`project-obsidian` дополнительно строит per-collection indexes в `<vault>/_generated/sourcefolio/collections/`: для каждой коллекции `<slug>.md` со списком видео и извлечёнными repositories (GitHub/GitLab) и hashtags, плюс `index.md`. Triage dashboards лежат в `.../dashboards/` (`inbox`, `keep`, `drop`, `done`) и используют Dataview-запросы над `ideas/`, поэтому **нужен плагин Obsidian Dataview**. `--scaffold-ideas` создаёт отсутствующие human notes в `<vault>/ideas/<source_id>.md` (frontmatter `status: inbox`) и не перезаписывает существующие; `scaffold-ideas` делает то же без пересборки generated views. `project-obsidian` также пересобирает `entities/`, `graph/` и `decisions/` (см. раздел «База знаний: human и generated зоны»).

Per-collection LLM digest:

```bash
sourcefolio llm-enrich \
  --collection Нейронки \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --limit 20
```

`llm-enrich` прогоняет latest captures коллекции через OpenAI-compatible chat model и пишет `llm-digest/v1` в `<vault>/_generated/sourcefolio/llm/<slug>.md`. Digest помечен `provenance: agent_inference` и warning-callout: это machine inference, не `creator_claim`. Конфигурация резолвится по приоритету: флаги `--base-url`/`--api-key`/`--model`, затем env `EV_LLM_BASE_URL`/`EV_LLM_API_KEY`/`EV_LLM_MODEL`, затем `data/llm.json` (ключи `base_url`/`api_key`/`model`), затем встроенные defaults. Ответы кэшируются в `data/derived/`; `--refresh` игнорирует кэш, `--dry-run` ничего не пишет.

Каталог репозиториев коллекции:

```bash
sourcefolio repo-catalog \
  --collection Нейронки \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`repo-catalog` извлекает ссылки на репозитории из latest captures коллекции (desc + комментарии), дедуплицирует по нормализованному `owner/repo` и обогащает GitHub-репозитории через REST API (endpoint `GET /repos/{owner}/{repo}` на `api.github.com`): stars, language, license, archived, last push, topics. Результат — note `repo-catalog/v1` в `<vault>/_generated/sourcefolio/repos/<slug>.md` с `provenance: external_observation`: таблица репозиториев плюс число видео и wikilinks на упомянувшие их source cards. GitLab-ссылки подсчитываются, но не обогащаются (v1 — только GitHub). Токен берётся из `--token` или env `GITHUB_TOKEN`; без токена лимит API — 60 запросов/час, а 403/429/404 изолируются как ошибка отдельного репозитория, прогон продолжается. Метаданные кэшируются в `data/derived/repos/`; `--refresh` игнорирует кэш, `--dry-run` ничего не пишет и не запрашивает.

Сопоставление сохранённого с проектами:

```bash
sourcefolio project-fit \
  --collection Нейронки \
  --projects data/projects.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`project-fit` прогоняет latest captures коллекции через OpenAI-compatible chat model и сопоставляет каждый элемент с проектами из `--projects` (JSON `{"projects": [{"id", "name", "description"}]}`). Результат — per-project briefs `project-fit/v1` в `<vault>/_generated/sourcefolio/projects/<slug>.md` плюс `projects/index.md` со списком проектов и числом глобально нерелевантных элементов. Каждый brief помечен `provenance: agent_inference` и warning-callout: это machine inference, не `creator_claim`.

`--collection` можно повторять; без него сканируется latest capture каждого источника в архиве (это может быть много). Совпадения накапливаются в `<derived-dir>/project-fit/index.json`: повторный запуск с другой коллекцией не затирает прежние briefs, а пересобирает все заметки из индекса. `--refresh` перезапрашивает модель и перезаписывает записи обработанных элементов; без него ответы берутся из кэша. `--max-ocr-kb` (по умолчанию 20) ограничивает объединяемый OCR-текст на элемент. `--dry-run` печатает план и первые промпты в stderr, ничего не записывая. Конфигурация модели резолвится так же, как у `llm-enrich`.

## База знаний: human и generated зоны

Vault делится на две зоны. Human-зона хранит канонические заметки, которые пишет пользователь. Generated-зона пересобирается командами и никогда не перезаписывает human-заметки.

Human-зона:

- `<vault>/ideas/`: заметки по сохранённым источникам (`<source_id>.md`, frontmatter `status: inbox`). Создаются `scaffold-ideas` или `project-obsidian --scaffold-ideas`.
- `<vault>/projects/`: профили проектов (`<project.id>.md`, frontmatter `type: project`, `status: draft`). Создаются `scaffold-projects`.
- `<vault>/decisions/`: решения (`<date>-<slug>.md`, frontmatter `type: decision`, `status: open`, `projects`, `tags`). Создаются `decision-new`.

Generated-зона `<vault>/_generated/sourcefolio/`:

- `source-cards/`, `overview/`, `collections/`, `dashboards/`, `llm/`, `repos/`: как описано выше.
- `entities/`: по одной заметке на GitHub-репозиторий (`<owner>__<repo>.md`, `type: repo`, `provenance: external_observation`). Метаданные берутся из кэша `repo-catalog`, плюс число упомянувших источников и связанные проекты.
- `graph/index.md`: knowledge graph на Mermaid (`type: graph`) с проектами, идеями, коллекциями и репозиториями. Строится из всех последних захватов (project→collection→idea→repo), а не только из совпадений `project-fit`; рёбра project→idea упорядочены по релевантности и ограничены; frontmatter сообщает `idea_shown` / `idea_total` / `idea_truncated`. Рендерится без плагинов.
- `decisions/index.md`: индекс human-решений (`decision-index/v1`) со ссылками на заметки в `decisions/`.
- `projects/`: per-project briefs `project-fit/v1` и `index.md`.

`project-obsidian` пересобирает source cards, overview, collection indexes, dashboards, а также `entities/`, `graph/` и `decisions/`. Mermaid-граф рендерится без плагинов; triage dashboards по-прежнему требуют плагин Dataview.

Команды:

```bash
sourcefolio scaffold-projects \
  --vault data/vault \
  --projects data/projects.json

sourcefolio decision-new \
  --vault data/vault \
  --title "Выбрать gallery-dl для слайдшоу" \
  --problem "Нужен надёжный resolver для photo posts" \
  --option "yt-dlp" \
  --option "gallery-dl" \
  --chosen "gallery-dl" \
  --rationale "Отдельный slideshow path с inventory checks" \
  --project sourcefolio \
  --tag ingestion

sourcefolio decision-search "gallery-dl" --vault data/vault

sourcefolio context \
  --vault data/vault \
  --derived-dir data/derived \
  --archive-dir data/archive \
  --projects data/projects.json \
  --format markdown

sourcefolio project-fit \
  --collection Нейронки \
  --projects data/projects.json \
  --profiles data/vault/projects \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault
```

`scaffold-projects` создаёт отсутствующие профили в `<vault>/projects/<project.id>.md` и не перезаписывает существующие. `decision-new` создаёт заметку решения в `<vault>/decisions/` (тоже без перезаписи); `decision-search` ищет по терминам (AND, casefold) и возвращает path/title/date/status. `context` собирает session brief из проектов, накопленных совпадений `project-fit` и решений; `--format markdown` печатает человекочитаемый вид, по умолчанию JSON, и дополнительно пишет бриф в `<vault>/_generated/sourcefolio/context/index.md` (+ `context.json`). Отсутствие файла `--projects` не роняет команду. `project-fit --profiles <dir>` подмешивает в промпт тело профиля `<project.id>.md` (без frontmatter) из указанной директории. `id` проекта обязан соответствовать `^[a-z0-9][a-z0-9-]*$`.

## Ответы, профили и refresh

`ask` — единая точка входа: retrieval через search + evidence-pack и, если доступна модель, grounded synthesis только по найденным excerpts. Без ключа или с `--no-llm` команда работает в retrieval-only режиме:

```bash
sourcefolio ask "какие подходы к RAG описаны в сохранённом" \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --format markdown

sourcefolio ask "какие подходы к RAG описаны в сохранённом" \
  --no-llm \
  --limit 10 \
  --excerpt-limit 3
```

`--project <id>` подмешивает имя, описание и профиль проекта в промпт и помечает заметку, но не меняет ranking retrieval. Каждый запуск пишет новую immutable заметку `answer/v1` в `<vault>/_generated/sourcefolio/answers/<UTC-stamp>-<slug>.md` (microsecond-stamped имя, `provenance: agent_inference`, warning-callout). Заметка пишется через `write_immutable_bytes`, а не через `replace_namespace`, поэтому `project-obsidian` её никогда не затирает. В заметке и в JSON-выводе есть copy-pasteable stub `sourcefolio decision-new ...`; human-решение командой не создаётся автоматически. `status: degraded` (модель недоступна) возвращает exit code 1 и оставляет retrieval excerpts.

`profile-draft` черновик human-профиля проекта из его собственного репозитория (README + `pyproject.toml`/`package.json`: name/description/dependency names/keywords). Один проект:

```bash
sourcefolio profile-draft \
  --repo /path/to/repo \
  --id my-project \
  --vault data/vault
```

Batch по всем проектам, у которых в `projects.json` задан `repo_path`:

```bash
sourcefolio profile-draft \
  --projects data/projects.json \
  --vault data/vault \
  --limit 10
```

Заметка пишется в `<vault>/projects/<id>.md` через `create_if_absent` и никогда не перезаписывает human-заметку. JSON-вывод: `{created, skipped, created_paths, projects_root, errors}`; нечитаемый репозиторий даёт exit code 1.

`refresh` — одна команда, которая последовательно переиспользует существующие handlers: `import-collections` (только при `--capture <file>`) → `project-obsidian` (всегда) → `project-fit` (только при `--fit`). stdout каждого шага захватывается, печатается один combined JSON `{"steps": [{"step", "exit", "output"}, ...]}`; шаг с ошибкой даёт `{"step", "error"}`. Exit code ненулевой, если упал любой шаг.

```bash
sourcefolio refresh \
  --capture /path/to/tiktok-collections-<stamp>.json \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault

sourcefolio refresh \
  --fit \
  --collection Нейронки \
  --projects data/projects.json \
  --vault data/vault
```

Daemon/watcher нет: расписание ручное — запускайте команду вручную или из планировщика с новым capture-файлом.

## Реестр кандидатов

Реестр превращает сохранённые captures в per-project очередь разработок, которые можно пустить в дело. Единица учёта — кандидат (инструмент, библиотека, репозиторий, техника, паттерн, модель, датасет, тема), а не видео. Полный контракт и калибровка — в [docs/REGISTRY.md](docs/REGISTRY.md).

Извлечь заякоренные упоминания из latest captures для проектов:

```bash
sourcefolio registry-extract \
  --projects data/projects.json \
  --profiles data/vault/projects \
  --collection Нейронки \
  --project sourcefolio \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --limit 25
```

`registry-extract` на каждую пару (источник, проект) создаёт маршрут `registry-route/v1` с вердиктом `candidate`/`irrelevant`/`insufficient_signal`. Каждое упоминание обязано нести дословную цитату (8–500 символов), символьный span и типизированный локатор (`description`/`title`/`comment`+`comment_index`/`ocr`+`extraction_id`/`caption`+`artifact_id`); упоминание без валидного якоря отбрасывается и считается в `dropped_anchors`. Конфигурация модели и кэш — как у `llm-enrich`; повтор с тем же `route_id` пропускается, `--refresh` перезапрашивает, `--dry-run` печатает первые два промпта в stderr и ничего не пишет.

Собрать снапшот, отрендерить очередь и записать решение:

```bash
sourcefolio registry-build \
  --projects data/projects.json \
  --derived-dir data/derived \
  --vault data/vault

sourcefolio registry-queue \
  --projects data/projects.json \
  --derived-dir data/derived \
  --vault data/vault \
  --project sourcefolio \
  --status inbox \
  --format markdown

sourcefolio candidate-decide \
  --candidate-id cand_<64-hex> \
  --status accepted \
  --approved-by Mind \
  --rationale "беру в пилот OCR" \
  --vault data/vault \
  --derived-dir data/derived
```

`registry-build` дедуплицирует упоминания по детерминированным ключам (репозиторий — `repo:<host>/<owner>/<repo>`, остальное — slug имени) и пишет content-addressed append-only снапшот `registry-snapshot/v1` в `data/derived/registry/snapshots/` с атомарным указателем `latest.json`; повторный прогон не мутирует принятые записи, а исчезнувший accepted-кандидат выживает как `stale`. Ручное слияние двух ключей — через `candidate-decide --merge-into <candidate_id>`. `registry-queue` упорядочивает кандидатов только внутри проекта (outcome → fit → score → mention_count → свежесть) и пишет generated-заметки `registry-view/v1` в `<vault>/_generated/sourcefolio/registry/`; межпроектного ранжирования нет. `candidate-decide` пишет human-заметку в `<vault>/candidates/<candidate_id>.md`: статус `inbox`/`accepted`/`rejected`/`deferred`, для любого кроме `inbox` обязателен `--approved-by` (auto-accept нет). Те же решения доступны в browser-виде «Проекты» через `GET /api/registry` и `POST /api/candidate` (заголовок `X-Sourcefolio: 1`).

## Локальный браузер архива

`render-ui` пишет самодостаточную HTML-страницу без медиа. `serve` поднимает локальный HTTP-сервер и отдаёт тот же интерфейс вместе с артефактами (превью и видео через Range) и записывает triage прямо из браузера:

```bash
sourcefolio serve \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --vault data/vault \
  --port 8765 \
  --open
```

Сервер слушает только `127.0.0.1`, печатает `{url, sources, vault}` и останавливается по Ctrl+C. `GET /` — та же проекция, что у `render-ui`; `GET /artifacts/<art_...>` — артефакт с `Accept-Ranges`; `POST /api/note` принимает `source_id`, `status` (`inbox`/`keep`/`drop`/`done`), `my_title`, `why_saved` и `clear` и требует заголовок `X-Sourcefolio: 1` (иначе 403) — это защита от записи со сторонних страниц. Правки идут в human-заметки `<vault>/ideas/<source_id>.md`, generated-зона не перезаписывается. После сохранения заголовок и статус на карточке и в drawer обновляются без перезагрузки; чтение заметки в Obsidian подхватывает изменения при следующем открытии.

## MCP-сервер

Установить extra и запустить сервер:

```bash
python -m pip install -e ".[mcp]"
sourcefolio-mcp
```

Сервер отдаёт CLI как MCP tools: `search`, `evidence_pack`, `import_collections`, `fetch_slides`, `project_obsidian`, `scaffold_ideas`, `llm_enrich`, `llm_catalog`, `repo_catalog`, `project_fit`, `archive_audit`, `capture_report`, `schema`, `context`, `scaffold_projects`, `decision_new`, `decision_search`. Каждый tool принимает те же параметры, что и соответствующая команда, и возвращает её stdout или `error (exit N)`. Инструменты помечены read/write (`read_only_hint`, `destructive_hint`, `open_world_hint`); `decision_new` требует непустой `title`; список проектов передаётся параметром `project_ids`.

Пример регистрации в `opencode.json` (локальный сервер, machine-specific путь к venv; файл gitignored):

```json
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "sourcefolio": {
      "type": "local",
      "command": [
        "D:/Sources/WhiteBite/Sourcefolio/.venv/Scripts/python.exe",
        "-m",
        "sourcefolio.mcp_server"
      ],
      "enabled": true
    }
  }
}
```

После правки `opencode.json` нужно перезапустить opencode, чтобы он подхватил сервер.

## Структура

```text
src/sourcefolio/
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
- [Реестр кандидатов](docs/REGISTRY.md)
- [Дорожная карта и gates](docs/ROADMAP.md)
- [План сравнительного пилота](docs/PILOT.md)
- [Real Tesseract на synthetic golden slides](docs/pilots/synthetic-ocr/README.md)
- [Public surrogate live TikTok ingest](docs/pilots/public-ingest/README.md)
- [Производительность и ограничители](docs/PERFORMANCE.md)
- [Исследование инструментов](docs/RESEARCH.md)
- [ADR](docs/adr/)

## Следующий шаг

Нужен пилот на реальном export пользователя и representative личном backlog, включая недоступные элементы и interrupted live resume. Public surrogate video/photo уже проверен, но не закрывает collection coverage и failure mix. TikTok-collections pipeline и LLM digest работают на synthetic/локальных данных; остаётся прогнать их на реальном личном export и collections и оценить качество digest и triage. Real Tesseract на known-text synthetic slides проверил OCR/resume/evaluation/integrity; остаётся разметить human-reviewed реальный OCR golden set и 10 реальных judged questions. ASR/keyframes подключаются через тот же derived contract после отдельных resource/quality gates. До измеренных результатов не добавляем GraphRAG и не запускаем два wiki-compiler в одном vault.
