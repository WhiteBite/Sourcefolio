# Дорожная карта и acceptance gates

План последовательный по обратной связи, но не предполагает переработку всего backlog. Каждый этап имеет условие, при котором следующий вообще нужен.

## M0 — Контракты и первый evidence slice ✅

**Сделано в этом проходе**

- Структура modular monolith + ports/adapters.
- Source identity, immutable capture, raw artifact refs.
- Потоковое content-addressed filesystem-хранилище.
- Идемпотентный `ArchiveCapture` use case.
- Typed evidence contract и JSON Schema export.
- Manual capture CLI, synthetic example, tests, architecture fitness test.

**Gate:** повторный импорт не дублирует данные; recapture сохраняет историю; большие файлы обрабатываются stream'ом; повреждённые или противоречивые records не считаются валидными.

## M1 — Реальный TikTok discovery и ingest bake-off 🚧

**Уже реализовано без выдумывания реальной схемы пользователя**

- Потоковый JSON parser для публично задокументированных `FavoriteVideoList` shapes и streaming TXT parser.
- `CaptureTask v1`, сохранение raw entries и явные collection fields без предположения, что официальный export их содержит.
- Дедупликация по stable TikTok ID, photo/slideshow detection, conservative short-link identity.
- Bounded async worker pool, backpressure, thread-offloaded archive, retry policy и resumable SQLite jobs/attempts.
- `tiktok-inspect` и metadata-only `tiktok-import`; последний честно создаёт partial source packets, не претендуя на скачанные видео.
- `yt-dlp` video adapter и `tiktok-download`: bounded subprocesses, site limit, timeouts/kill, disk/byte limits, sanitized metadata и resume. Есть fake-executable regression и отдельный [live public surrogate](pilots/public-ingest/README.md): 1 video complete, resume skip, raw/artifact hashes и byte audit совпали.
- Отдельный `gallery-dl` slideshow resolver и `tiktok-slides`: только photo tasks, ordered images, slide/total limits, inventory/completeness checks и честный partial при непроверяемом числе слайдов. На одном live public photo post получено 3 ordered images и complete packet; это ещё не representative пользовательский corpus.
- Read-only `capture-report` из существующего SQLite: status/kind counts, attempts/retries, finished-attempt p50/p95, stale running candidates и recent partial/failed errors без миграции схемы.
- `collection-capture/v1` path: Tampermonkey userscript собирает коллекции из залогиненной веб-сессии (full/incremental, optional top-20 comments), `import-collections` архивирует metadata-only packets с resumable bounded workers. Это отдельный discovery-путь, не заменяющий официальный export.
- `refresh`: последовательный прогон `import-collections` (при `--capture`) → `project-obsidian` → `project-fit` (при `--fit`) с одним combined JSON; переиспользует существующие handlers, без daemon — расписание ручное.

**Осталось проверить на реальных данных**

1. Получить официальный export пользователя и выбрать representative URL из него; два public surrogate URL уже проверены отдельно и не раскрывают поля личного export.
2. Проверить, содержатся ли collection names/IDs, favorites, timestamps и доступные поля.
3. Отдельно сравнить 1–2 ingestion-пути на одной-двух коллекциях: официальный export + resolver, Rot2Gold и/или Dewey; gallery-dl/отдельный slideshow resolver там, где нужен.
4. Расширить проверку полноты gallery-dl metadata с одного успешного public photo post на representative slideshow и unavailable/rate-limited cases; неизвестный inventory по-прежнему остаётся partial.
5. Все варианты производят одинаковые `SourcePacket v1`; failures уходят в control plane.

**Golden sample:** видео, slideshow, дубль, удалённый/недоступный элемент, короткая ссылка, одинаковый источник в разных коллекциях.

**Gate:** известна полнота метаданных, процент успешного архивирования по типам, причины ошибок и стоимость ручного review. До этого нет массового backfill.

## M2 — Дешёвая extraction и качество 🚧

**Реализовано**

- `ExtractionRecord v1` и `ExtractionRequest`: typed `slide`/`timestamp`/`text_span` locators, excerpt, extractor provenance/config, immutable derived storage.
- Request ID включает capture/artifact/kind/locator/extractor/config; повторный OCR с той же операцией пропускается, смена версии/config создаёт новую.
- Tesseract OCR adapter: bounded input/output, timeout/process-group kill, bounded subprocess concurrency, fail-fast missing executable.
- Derived OCR text подключён к общему search без изменения raw SourcePacket.
- Общий application runner для `ocr-capture`/`ocr-backlog`: collection/kind/status filters, latest-per-source по умолчанию, capture/slide limits, bounded concurrency, failure isolation, resume и item p50/p95 + wall metrics.
- Immutable `ocr-backlog-report/v1` с runtime/extractor parameters и per-item resume history; simulated worker interruption recovery test.
- `ocr-evaluation/v1` + `ocr-evaluate`: stable golden locators, optional request pinning, versioned normalization, exact/CER/WER/empty и explicit failed/missing semantics.

**Осталось**

- Human-reviewed разметка реального пользовательского OCR golden set и измерение качества; [real Tesseract на 6 synthetic slides](pilots/synthetic-ocr/README.md) уже проверил execution/resume/evaluation, но не этот quality gate. Далее — версионированные ASR/keyframes adapters с resource gates.
- Persistent OCR queue/multiprocess leases только при измеренной необходимости; текущий backlog runner их не эмулирует.
- Извлечение `creator_claim` отдельно от `agent_inference`.
- Candidate entities и дубли без автоматического human-owned merge.

**Gate:** golden set вручную проверен по extraction precision/recall, нераспознанным элементам и времени исправления. Плохой extraction не компенсируется красивой wiki.

## M3 — Baseline A: source cards + search + on-demand answers 🚧

**Реализовано и покрыто тестами**

- Машинно пересобираемые Obsidian source cards из canonical `SourcePacket`; YAML properties, limitations, provenance и artifact links.
- OCR-enriched cards и generated overview с coverage/status/kind/collection/latest-capture views; derived excerpts сохраняют locators и extraction provenance.
- Generated namespace отделён от human-owned notes; rebuild заменяет только собственный namespace и удаляет stale generated documents.
- Deterministic lexical search по общим packets, bounded textual artifacts и derived OCR text, с фильтрами и title/collection weighting. Score — relevance, не trust.
- `evidence-pack` JSON/Markdown: ranked sources, snippets, raw artifact links, typed derived locators, provenance и limitations без автоматического LLM-ответа.
- `evidence-pack-verify` + `evidence-pack-integrity/v1`: source/artifact/extraction reference и provenance checks, optional byte hashes, structured issues.
- `search-evaluation/v1` и `search-evaluate`: hit rate, MRR, recall, precision@k и per-query ranking для записанных judged questions.
- Per-collection indexes (`collection-index/v1`) с извлечением repositories (GitHub/GitLab) и hashtags; triage dashboards (`triage-dashboard/v1`, `inbox`/`keep`/`drop`/`done`) на Dataview и human `ideas/` scaffolding без перезаписи.
- `llm-enrich`: per-collection `llm-digest/v1` через OpenAI-compatible chat model, cache и явная пометка `agent_inference`.
- MCP-сервер `sourcefolio-mcp`: CLI как MCP tools для search, evidence packs, collections, Obsidian projection и operational reports.
- `project-fit`: сопоставление latest captures с проектами через chat model, накопительный индекс совпадений и per-project briefs `project-fit/v1`.
- Generated `entities/` (repo nodes) и `graph/` (Mermaid knowledge graph) как rebuildable views.
- `ask`: on-demand grounded answer — retrieval через search + evidence-pack и optional grounded synthesis только по excerpts; `provenance: agent_inference`, citations, immutable per-run note `answer/v1` в `answers/`; relevance не истинность, human decision stub не создаётся автоматически.

**Ещё не доказано на реальном корпусе**

- 10 реальных вопросов пользователя с evidence-backed ответами и ссылками на фрагменты.
- On-demand research первоисточников для выбранных вопросов.
- Hybrid/QMD только если простой поиск реально недостаточен по записанным метрикам.

**Gate:** записаны answer quality, unsupported claims, self-citation, время поиска/review и повторное использование. Это baseline для любого усложнения.

## M4 — Решения, личный контекст и outcomes 🚧

**Реализовано**

- Human `decisions/` notes: `decision-new` создаёт заметку (вопрос, варианты, выбранный путь, причина, follow-ups, projects, tags) без перезаписи; `decision-search` ищет по терминам.
- `context`: session brief из проектов, накопленных совпадений `project-fit` и решений; JSON или Markdown.
- Human `projects/` profiles: `scaffold-projects` создаёт черновики, `project-fit --profiles` подмешивает профиль в промпт.
- Generated `decisions/index.md` как rebuildable view.
- `profile-draft`: repo-seeded черновик human-профиля проекта (README + `pyproject.toml`/`package.json`) с `create_if_absent`, без перезаписи human-заметки.

**Осталось**

- Outcome отдельно от predictions и рекомендаций.
- Evidence и revisit conditions внутри решения.
- «Почему сохранил» по умолчанию unknown; inferred intent как отдельная запись.
- Явные agent proposals отдельно от human-owned context entries.

**Gate:** 3–5 реальных решений можно реконструировать: на каком основании выбран вариант и что произошло потом.

## M5 — Сравнение wiki-компиляторов

На **одних и тех же source packets** для 40–60 стратифицированных элементов:

- A — baseline;
- B — `claude-obsidian`;
- C — `llm-wiki-compiler`.

Не запускать B и C одновременно в одном vault. Использовать отдельные копии/projection roots.

**Gate:** B или C принимаются только при значимом превосходстве над A по качеству ответов, provenance, review minutes и пересобираемости. Иначе остаётся A.

## M6 — Selective synthesis и automation

- Synthesis только при активном вопросе, проекте, решении, противоречии или значимом повторяющемся мотиве.
- Rebuild по evidence basis; lint для unsupported claims и stale sources.
- Отдельные workers/queues только при измеренной необходимости.
- OKF export как опциональный interoperability profile, не внутренняя зависимость.

## Постоянные инженерные gates

- Новый адаптер не меняет доменные ID ради своего удобства.
- Каждый extractor/generator версионируется.
- Каждый generated synthesis можно удалить и пересобрать.
- Human-owned записи не перезаписываются автоматически.
- Тесты включают дубли, противоречия, повторный запуск и недоступные источники.
- Изменения контракта получают новую версию и migration/reader policy.
