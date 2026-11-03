# Доменные контракты

Машинные схемы находятся в `contracts/`, но нормативный смысл полей и инвариантов фиксируется здесь. Идентификаторы не зависят от Obsidian, TikTok downloader, SQLite или конкретной LLM.

## 1. Source identity

`SourceIdentity` содержит:

- `platform` — например `tiktok`, `github`, `web`, `manual`;
- `kind` — video, slideshow, web page, repository, document, text или other;
- `external_id` — устойчивый ID внутри платформы, если известен;
- `canonical_url` — канонизированный adapter'ом locator, если доступен.

Нужен хотя бы `external_id` или `canonical_url`. Если external ID известен, `source_id` строится из `(platform, external_id)` и не меняется при смене URL. Если его нет, используется canonical URL. Короткие ссылки и tracking-параметры должен разрешать ingestion adapter, а не домен угадывает их сам.

Если сначала известен только URL, а позже обнаружен external ID, deterministic key может измениться. Это не должно молча «переключать» прошлую историю: понадобится явная identity resolution/alias-запись, при ручном объединении — human-owned. Поэтому ingestion adapter обязан разрешать stable external ID как можно раньше, а интерфейс не должен автоматически объединять два source ID только из-за похожего title.

## 2. ArtifactRef

Ссылка на неизменяемые байты:

- `artifact_id = art_<полный sha256>`;
- `sha256`, `size_bytes`, `media_type`;
- `role` — original video/audio/image, caption, page snapshot, author metadata, attachment или other;
- `original_filename`, если был.

Raw artifact и derived extraction не смешиваются. OCR хранится в отдельном `ExtractionRecord v1` со ссылкой на capture/artifact и generator provenance; ASR и embeddings должны использовать свои versioned derived records, а не добавляться в raw packet.

## 3. SourcePacket v1

Нормализованная запись одного capture:

| Поле | Смысл |
|---|---|
| `schema_version` | сейчас `source-packet/v1` |
| `source_id` | стабильная логическая идентичность |
| `capture_id` | hash нормализованного содержимого capture |
| `source` | SourceIdentity |
| `captured_at` | timezone-aware время capture |
| `collector` | кто/что и каким методом собрал данные |
| `status` | `complete` или `partial` |
| `limitations` | обязательны для `partial` |
| `title` | title из источника, не synthesized title |
| `collections` | memberships на момент capture |
| `artifacts` | упорядоченные raw artifact refs |
| `metadata` | JSON-compatible platform-specific данные без секретов |

Инварианты:

1. Хотя бы один raw artifact. Полностью failed download — это failed job, не complete packet.
2. Для `partial` нужна хотя бы одна limitation; `complete` не прячет limitations.
3. Все timestamps timezone-aware и нормализуются к UTC.
4. ID проверяются против содержимого при чтении; нельзя silently изменить packet, сохранив прежний ID.
5. Metadata не является местом для cookies, токенов и secrets.

### Идемпотентность

Одинаковый capture означает одинаковые нормализованные поля, включая `captured_at`. Adapter должен сохранять фактическое capture time, а не подменять его текущим временем при retry. Новый recapture законно получает новый `capture_id`.

## 4. SourceRef

Указатель на доказательный фрагмент:

- `source_id`, `capture_id`;
- опционально `artifact_id`;
- опционально `locator` с namespaced grammar, например `timestamp:12.5-18.0`, `slide:3`, `text:120-145`;
- опционально `excerpt` для быстрой проверки.

В `SourceRef v1` locator остаётся строкой для совместимости. Новый derived-text контур уже использует typed `EvidenceLocator` с проверенными полями; при будущей версии EvidenceItem можно сослаться на него без изменения identity источника.

## 5. EvidenceItem v1

| Поле | Смысл |
|---|---|
| `evidence_id` | content-derived ID записи |
| `kind` | creator claim / agent inference / external observation / user outcome |
| `statement` | атомарное утверждение или наблюдение |
| `source_refs` | непосредственные source pointers |
| `supporting_evidence_ids` | basis для вывода |
| `provenance` | кто и как создал запись, версия/model при необходимости |
| `approval` | человеческое подтверждение для user outcome, если запись создал не пользователь |
| `created_at` | время создания записи |

Правила:

- creator claim и external observation требуют хотя бы один source ref;
- agent inference требует agent provenance и непустой basis;
- user outcome либо создан пользователем, либо имеет явное approval;
- прямое self-citation запрещено;
- записи immutable, поэтому цикл нельзя получить редактированием старой записи; будущие derived records обязаны сохранять append-only свойство;
- отсутствие `confidence` намеренно: extraction confidence, вероятность вывода и эпистемическое доверие — разные вещи и не сводятся к одному числу.

## 6. Verification и независимость

Verification — отдельная будущая append-only запись, а не edit evidence. Она должна содержать:

- target;
- supports / contradicts / mixed / unverified;
- method и verifier provenance;
- evidence/source basis;
- source family attributions;
- время проверки и freshness, если применимо;
- заметку о противоречиях.

Два TikTok-ролика, пересказывающих один пресс-релиз, — одна source family для независимого подтверждения. Agent inference не превращается во external observation от цитирования самого себя. High-risk claims требуют более строгой политики, но она задаётся явно для контекста, а не универсальным score.

## 7. Intent

Отсутствие пользовательского намерения означает `unknown`; не нужно создавать фиктивную запись «unknown» для каждого источника.

- `user_stated_intent` — human-owned запись;
- `inferred_intent` — отдельное agent proposal с rationale;
- подтверждение inference создаёт пользовательскую запись, а не перекрашивает происхождение старой.

## 8. Personal context

Будущие human-owned entries: goal, project, stack, constraint, taste, experience. Записи имеют validity interval и supersedes-ссылку вместо перезаписи прошлого. Agent может предложить entry, но canonical status получает только после approval.

## 9. Decision и Outcome

`Decision` должен позволять реконструировать:

1. вопрос;
2. рассмотренные варианты;
3. evidence basis;
4. выбранный вариант и причину;
5. кто решил;
6. условия пересмотра;
7. последующий outcome.

Outcome не подменяется прогнозом агента. Пользовательский негативный опыт не удаляется из-за более поздней положительной рекомендации: сохраняются оба наблюдения с датами и контекстом.

## 10. CaptureTask и operation hash

`CaptureTask v1` — discovery-запись о сохранённом элементе, ещё не доказательство содержимого видео. Он содержит SourceIdentity, optional saved_at, collections, один или несколько raw export entries и parser metadata.

- `task_id` строится от source identity и объединяет повторные export entries одного источника.
- `input_hash` зависит от raw entries, коллекций и остального нормализованного входа.
- `operation_hash` дополнительно включает adapter operation key, поэтому metadata-only и media capture — разные операции над одной задачей.
- Raw entry artifact позже включается в SourcePacket: даже недоступное видео остаётся представлено в backlog без выдуманного caption или content.
- Неизвестные поля export сохраняются. Naive date без явно заданного часового пояса не превращается молча в UTC.
- Job `succeeded` не означает SourcePacket `complete`: первое описывает выполненную операцию, второе — полноту доказательного capture.

## 11. ExtractionRequest и ExtractionRecord v1

Derived text не является `creator_claim`: OCR/ASR только представляет распознанное содержимое с указанием инструмента и ограничений.

- `ExtractionRequest` содержит source/capture/artifact IDs, extraction kind, typed locator, extractor provenance и JSON config. Его `request_id = exq_<hash>` является cache/resume key.
- `ExtractionRecord` содержит request, `request_id`, `created_at`, полный text, проверяемый normalized excerpt, limitations и content-derived `extraction_id = ext_<hash>`.
- Повтор той же request operation возвращает существующий record без запуска инструмента. Другая модель, версия, language/config или locator создают другой request.
- `slide` требует положительный номер; `timestamp` и `text_span` задают упорядоченные half-open границы и не смешивают поля разных типов.
- Records immutable и лежат в `data/derived/extractions/`, а не внутри SourcePacket. Raw capture остаётся неизменным.
- Search может индексировать derived text, но не превращает его в независимое доказательство истинности.

## 12. OCR batch reports и quality evaluation

`ocr-backlog-report/v1` — immutable operational record одного запуска, не knowledge/evidence record. Query/runtime limits, extractor provenance/config, selection и per-item outcomes позволяют сопоставить created/skipped/failed между запусками. Failed item не получает фиктивный request/extraction ID; successful resume item сохраняет реальные IDs.

`ocr-evaluation/v1` описывает golden sample через `capture_id`, `artifact_id`, typed locator и `expected_text`. Optional `request_id` выбирает строго одну operation; без него evaluator детерминированно берёт последнюю подходящую запись и сообщает о multiple versions.

`ocr-evaluation-report/v1` использует версионированную NFKC/casefold/whitespace normalization:

- exact, CER и WER рассчитываются только для samples с выбранным record;
- empty rate имеет знаменатель evaluated samples;
- failed rate включает только явные failures из supplied batch reports;
- missing без такого report не переименовывается в failed;
- oversized samples не получают выдуманные метрики;
- CER/WER могут быть больше 1 при пустом/коротком reference и не измеряют factual correctness.

## 13. Retrieval evidence pack

`evidence-pack/v1` — rebuildable retrieval view, а не новый canonical evidence item и не synthesized answer.

- Query и filters воспроизводимо ранжируют captures через lexical baseline; rank/score означают только relevance.
- Каждый result сохраняет `source_id`/`capture_id`, исходные artifact refs с SHA-256 и local URI, collector provenance, capture limitations и search snippet.
- Derived excerpts ссылаются на `ExtractionRecord` через extraction/request IDs и содержат typed `EvidenceLocator`, extractor provenance/config и собственные limitations.
- `matched_query` описывает только совпадение терминов в конкретном derived text; оно не повышает trust и не превращает OCR в creator claim.
- Pack обязан явно сохранять retrieval/extraction limitations. Он не создаёт `creator_claim`, `agent_inference`, verification или answer автоматически.
- `evidence-pack-integrity/v1` — отдельный operational report о ссылочной/byte integrity. Он не изменяет pack или archive и не является semantic verification.

## 14. Synthesis

Synthesis — rebuildable derived object с:

- вопросом/областью;
- упорядоченным независимым от генерации evidence basis;
- hash basis;
- generator + model + prompt/config version;
- generated_at;
- review status и human review отдельно;
- ссылками на поддерживающие и противоречащие evidence.

При изменении basis создаётся новая версия. Dashboard или synthesis не становятся каноническим фактом только потому, что красиво отрендерены в Obsidian.
