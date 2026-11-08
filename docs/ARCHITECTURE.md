# Архитектура

## 1. Система в одном изображении

```text
TikTok / manual / web / GitHub
             │
             ▼
      Capture adapters ────── failures ──▶ Control plane
             │                              (jobs, retries, cache,
             ▼                               processing versions)
  Immutable evidence archive
  raw artifacts + source packets
             │
             ▼ cheap, versioned extraction
  Claims / OCR / ASR / entity candidates
             │
             ├──────────────▶ Baseline A: search + retrieval evidence packs
             │                  (on-demand answers — отдельный gate)
             │
             ▼ selective compilation / review
  Human-owned Obsidian vault                 Rebuildable views
  context / entities / decisions             indexes / dashboards /
  outcomes / reviewed syntheses              drafts / exports
```

Это четыре независимых контура, а не один обязательный pipeline:

1. **Backfill** старого backlog — останавливаемый и выборочный.
2. **New saves** — быстрый архив новых элементов без ожидания backfill.
3. **On-demand research** — глубокий разбор только активного вопроса.
4. **Review / synthesis / outcomes** — периодическая пересборка и проверка пользы.

## 2. Пять плоскостей данных

| Плоскость | Что содержит | Каноничность | Где живёт |
|---|---|---|---|
| Evidence archive | исходные файлы, snapshots, source packets, указатели на фрагменты | captures неизменяемы; новая версия добавляется | `data/archive/` |
| Derived evidence | OCR/ASR text, typed locators, extractor config/version | append-only и пересобираемо из evidence | `data/derived/` |
| Knowledge vault | identity, ручные связи, личный контекст, решения, outcomes, reviewed syntheses | канонические человеческие записи | Obsidian-compatible Markdown + YAML |
| Control plane | jobs, attempts, retries, leases, cache keys, processing versions | операционное состояние, не знание | отдельно, вероятнее SQLite |
| Views / exports | source cards, indexes, dashboards, черновые syntheses, OKF | пересобираемые представления | отдельные generated-пути |

**Control plane ≠ knowledge plane.** Obsidian не используется как очередь или lock-manager.

Код уже покрывает evidence archive, derived OCR records, доменные контракты, первый SQLite control plane и rebuildable views. Human-owned knowledge plane и LLM/on-demand research не создаются заранее без соответствующего use case.

## 3. Модульный монолит с ports & adapters

На старте выбран модульный монолит, а не микросервисы:

```text
domain ◀── application ◀── adapters ◀── CLI / worker / future API
```

Фактические Python-зависимости направлены внутрь:

- `domain` не импортирует `application` или `adapters`;
- `application` зависит от домена и собственных `Protocol`-портов;
- filesystem/TikTok/SQLite/Obsidian/LLM адаптеры зависят от контрактов;
- composition root связывает реализации.

Это позволяет заменить загрузчик или wiki-движок без смены ID и архива. Если позже отдельный OCR/ASR worker понадобится для ресурсоёмкой обработки, он использует те же контракты; разбиение не требует смены домена.

## 4. Capture и идентичность

- `source_id` стабилен для логического источника. При наличии platform external ID он важнее изменчивого URL.
- `capture_id` вычисляется из полного нормализованного содержимого source packet.
- `artifact_id = art_<sha256>`; одинаковые байты дедуплицируются.
- Capture неизменяем. Повторное снятие источника создаёт новый capture с тем же `source_id`.
- Повтор того же capture идемпотентен.
- Порядок артефактов сохраняется: он значим для слайдшоу.
- `partial` capture обязан явно перечислить limitations. Ошибка загрузки сама по себе относится к control plane и не маскируется под полноценный source packet.

### Почему не единый `content_hash` для всего

Логический источник, конкретный capture и байтовый артефакт — разные identity. Смешение этих уровней мешает recapture, истории и дедупликации.

## 5. Потоковость, атомарность и сбои

- Адаптер хранения читает артефакт как stream и считает SHA-256 по chunks; видео не помещается целиком в RAM.
- Сначала данные пишутся во временный файл в том же filesystem, затем атомарно появляются в immutable location без overwrite.
- Source packet сохраняется после артефактов, поэтому опубликованный packet не ссылается на ещё не записанные байты.
- Repository повторно валидирует packet на границе записи: даже мутация вложенного JSON после создания объекта не позволяет сохранить stale ID.
- `verify(artifact_id)` отдельно сверяет текущие bytes с content address; это позволяет audit/reconciliation обнаруживать повреждение без обязательного double-read при каждом просмотре.
- Если процесс упал до packet, возможен безвредный orphan artifact. Его можно найти reconciliation-процедурой; нельзя удалять автоматически без отдельной политики.
- Повторная derived обработка использует request ID из capture/artifact + extraction kind + locator + extractor version/config; raw capture ID не меняется.

### Конкурентность, backpressure и resume

- Фиксированный async worker pool ограничивает network/subprocess operations; bounded queue не даёт backlog создать неограниченное число coroutines.
- Файловый hash и archive вызываются через `asyncio.to_thread`; event loop не ждёт диск.
- CPU-heavy OCR/ASR не маскируются под I/O: для них позже нужен отдельный bounded process pool.
- Retry выполняется только для `CaptureError(retryable=True)` с exponential backoff + jitter. Неожиданные ошибки не зацикливаются автоматически.
- `task_id` стабилен для source, а `operation_hash` включает task input и adapter operation key. Смена metadata-only на media capture не наследует неправильный skip.
- SQLite хранит jobs/attempts и crash recovery отдельно от evidence. Succeeded operation пропускается, partial/failed повторяется.
- Успех операции и полнота capture различны: metadata-only job может быть `succeeded`, а packet остаётся `partial`.
- `capture-report` — read-only projection над `capture_jobs`/`capture_attempts`: status/kind counts, retry history, finished-attempt p50/p95, stale running candidates и sanitized current partial/failed errors. Схема SQLite не меняется.
- Пока поддерживается один активный runner на control DB; multiprocess leases появятся только с отдельной необходимостью и тестами.

### Derived extraction

- `ExtractText` принимает canonical packet + artifact + typed locator и не изменяет raw packet.
- Adapter сначала resolve'ит tool/model version, затем вычисляется request cache key. Операция с неизвестной provenance не получает ложный стабильный cache key. Для Tesseract supported `--tessdata-dir` добавляет SHA-256 traineddata; без него limitation остаётся явной.
- `ExtractionRecord` хранится immutable в derived plane; повторный request возвращает существующий результат. Failed extraction не кэшируется как успешная.
- Tesseract выполняется как subprocess без shell с bounded input/output, timeout/process-group kill и ограниченным числом параллельных workers.
- Общий application `OcrBacklogRunner` обслуживает и `ocr-capture`, и `ocr-backlog`: filters, latest-per-source по умолчанию, capture/slide limits, bounded chunks, failure isolation, request resume и p50/p95/wall metrics. Это не persistent OCR queue и не multiprocess lease protocol.
- Optional `--report` сохраняет immutable `ocr-backlog-report/v1`: query/runtime/extractor config, selection, wall/item metrics и per-item created/skipped/failed с request/extraction IDs. Разные запуски используют разные report paths, поэтому resume history не перезаписывается.
- `OcrEvaluator` сопоставляет golden samples по capture/artifact/typed locator, optional request ID и derived records. Нормализация версионирована (`NFKC + casefold + whitespace`); exact/CER/WER/empty считаются только для evaluated records. Failed берётся из явных batch report items, а не угадывается по отсутствию record.
- OCR — derived observation, не creator claim и не agent inference. ASR/keyframes должны добавляться через отдельные adapters и acceptance gates.
- `archive-audit` read-only сверяет packet references с artifact bytes и сообщает orphans; удаление требует отдельной политики и не входит в audit.

## 6. Evidence вместо «правды»

Source packet доказывает содержимое источника, не его истинность. Evidence item имеет явный тип:

- `creator_claim` — утверждение, извлечённое из источника;
- `agent_inference` — вывод агента с обязательным basis;
- `external_observation` — наблюдение из внешнего источника;
- `user_outcome` — результат, сообщённый или подтверждённый пользователем.

Нет универсального `trust_score`. Проверки хранятся отдельно и сохраняют supports/contradicts/mixed, дату, метод и source families. Повторы одного первоисточника не становятся независимыми подтверждениями. Synthetic answer не является собственным внешним подтверждением.

Evidence items append-only; выводы пересматриваются новыми verification/review-записями, а не скрытым редактированием истории.

## 7. Obsidian

Obsidian остаётся основным кандидатом человекочитаемого интерфейса, но не «всей базой»:

- human-owned Markdown + YAML — каноничны;
- media и большие source packets лежат вне индексируемого vault;
- в vault попадают лёгкие ссылки/карточки и отобранные знания;
- Bases строит представления над properties;
- generated files маркируются basis и generator version;
- ручные заметки не перезаписывает renderer.

`project-obsidian` читает canonical packets и optional derived records через repository ports. Он пишет source cards в `<vault>/_generated/evidence-vault/source-cards/` и coverage/collection/latest-capture dashboard в `.../overview/index.md`. Cards содержат extraction/request IDs, typed locators, excerpts, extractor provenance и artifact links. Projection store сначала заполняет staging directory, затем заменяет только собственный namespace; это не операция над человеческими заметками. YAML properties пригодны для Obsidian Bases, а artifact links ведут в evidence archive.

Vault разделён на две зоны. Human-зона (`ideas/`, `projects/`, `decisions/`) хранит канонические заметки пользователя: `ideas/<source_id>.md` (`status: inbox`), `projects/<project.id>.md` (`type: project`, `status: draft`), `decisions/<date>-<slug>.md` (`type: decision`, `status: open`, `projects`, `tags`). Generated-зона `<vault>/_generated/evidence-vault/` пересобирается и не трогает human-заметки. Кроме source cards, overview, collection indexes, dashboards, `llm/` и `repos/`, `project-obsidian` строит `entities/` (по заметке на GitHub-репозиторий, `type: repo`, `provenance: external_observation`), `graph/index.md` (Mermaid knowledge graph, `type: graph`) и `decisions/index.md` (индекс human-решений). Generated-заметки несут `generated: true`, `generator`, `generator_version` и `schema_version`; repo- и graph-заметки дополнительно помечаются `type` и `tags` (`evidence-vault`, `evidence-vault/<kind>`). Mermaid-граф рендерится без плагинов; triage dashboards требуют Dataview.

`search` строит deterministic in-memory lexical index из тех же packets и optional derived OCR records. В ranking входят термины, title и collection names; capture timestamp и capture ID задают воспроизводимый tie-break. Relevance score не является trust score. Binary media не читается, размер каждого textual artifact/derived record ограничен; общий RSS индекса при этом пока не ограничен отдельным лимитом.

`evidence-pack` использует тот же search baseline и возвращает JSON или Markdown retrieval pack: ranked captures, search snippets, raw artifact URIs/SHA-256, collector provenance, derived excerpts с extraction/request IDs, extractor config, typed locators и limitations. `matched_query` отдельно показывает, найден ли запрос в конкретном derived excerpt, а не только в caption/metadata. Pack не содержит synthesized answer и не подтверждает истинность claims.

`evidence-pack-verify` принимает сохранённый `evidence-pack/v1` и не пересчитывает ranking. Сначала contract проверяет typed locators и внутренние parent refs, затем verifier сравнивает source/artifact metadata, extraction request/excerpt/provenance и `matched_query` с canonical packets/records. Optional `--verify-bytes` проверяет content-addressed bytes. Невалидная schema отклоняется при загрузке; валидный pack с dangling/tampered refs получает structured issues и exit code 1.

До пилота нельзя выбирать `claude-obsidian` или `llm-wiki-compiler` как обязательный центр. Baseline A обязан оставаться полноценным вариантом.

## 8. Безопасность и приватность

- Локальный архив по умолчанию; внешние LLM/OCR/ASR — явные адаптеры, а не скрытое условие.
- Prompt/source content считается недоверенным входом. Инструкции из видео, README или webpage не получают автоматически право запускать действия.
- Агентные research-действия ограничиваются разрешёнными tools и областью записи.
- Секреты и cookies не попадают в source packets, Markdown, logs или test fixtures.
- Внешние ссылки и локальные media разделяются; generated HTML не должен требовать сети для базового просмотра.

## 9. Что намеренно не построено сейчас

- Нет TikTok scraping до проверки реального export и коллекций.
- Нет multiprocess leases: текущий SQLite runner рассчитан на один активный процесс.
- Нет LLM prompts до golden set и baseline-метрик.
- Нет GraphRAG/vector DB по умолчанию.
- Нет автоматических записей в human-owned vault.
- Нет попытки спроектировать все будущие сущности кодом заранее: сначала контракт и acceptance criteria, затем вертикальный срез.
