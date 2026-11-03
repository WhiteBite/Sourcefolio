# Сравнительный пилот

## Цель

Проверить, даёт ли сложная wiki реальную пользу сверх простого поиска по хорошо сохранённым источникам. Не сравнивать «красоту заметок», а качество ответов, решений и проверяемость.

## Этап 1 — ingest отдельно от knowledge

На 1–2 коллекциях сравнить доступные ingestion-пути. Для каждого измерить:

- сохранение collection membership;
- видео и slideshow coverage;
- caption/author/date metadata;
- повторный запуск и дедупликацию;
- недоступные/удалённые элементы;
- ручные действия и cookies/account risk;
- скорость и стоимость;
- долю неясных failures.

Результат каждого варианта — общий SourcePacket v1. Это исключает сравнение knowledge-движков на разных входных данных.

## Этап 2 — стратифицированный golden set

40–60 элементов, намеренно включающих:

- фильмы/сериалы/аниме;
- AI news и GitHub repositories;
- идеи и стартапы;
- видео и слайдшоу;
- дубли и репосты одного первоисточника;
- повторяющиеся темы;
- противоречащие рекомендации;
- неполные или недоступные captures;
- элементы с неизвестным намерением пользователя.

На подмножестве вручную разметить ожидаемые сущности, утверждения, дубли, source families и допустимые ответы. Golden set — пользовательский актив, не ephemeral test fixture.

## Этап 3 — три варианта

| Вариант | Состав |
|---|---|
| A | source cards + простой search + on-demand evidence-backed answers |
| B | `claude-obsidian` как knowledge/provenance layer |
| C | `llm-wiki-compiler` как compiler с profiles/review/eval |

B и C выполняются в отдельных projection roots. Ни один не получает права перезаписывать human-owned fixtures.

## Готовый технический checklist пилота

1. `tiktok-inspect` → зафиксировать export fields/issues.
2. `tiktok-import` → создать resumable metadata backlog.
3. `tiktok-download --limit 10` и отдельно `tiktok-slides --limit 10` → сравнить complete/partial/failed по типам.
4. Повторить обе operations и прервать небольшой batch → проверить skip/retry/recovery.
5. `capture-report` → зафиксировать status/kind counts, retries, p50/p95, stale candidates и recent errors; `archive-audit --verify-bytes` → missing/corrupt/orphans без удаления.
6. Для выбранных slideshow captures выполнить `ocr-capture`, затем маленький `ocr-backlog --report`; повторить с новым report path и проверить skipped requests, failure isolation и metrics.
7. Разметить golden `ocr-evaluation/v1` и запустить `ocr-evaluate` со всеми relevant batch reports → зафиксировать exact/CER/WER/empty/failed/missing по extractor version; не смешивать missing с failed без report.
8. `project-obsidian` → проверить OCR-enriched cards и overview; `search`/`evidence-pack` → проверить, что каждый excerpt ведёт к нужному artifact/locator и limitations видны; сохранённый pack проверить через `evidence-pack-verify --verify-bytes`.
9. `search-evaluate` на judged questions → сохранить JSON report и review minutes. Evidence packs не считать готовыми evidence-backed answers: реальные ответы остаются отдельным review gate.

Живые tools сначала запускать на маленькой выборке; fake-executable tests не заменяют этот checklist.

## Уже выполненные autonomous surrogate checks

Эти проверки подготавливают пилот, но не закрывают пользовательские gates:

- [Public live ingest](pilots/public-ingest/README.md): настоящие `yt-dlp`/`gallery-dl`, один public video и один public photo post; оба complete, повторы skipped, raw packet + artifact hashes неизменны, byte audit OK. Личный export, unavailable/restricted cases и прерванный live download ещё не проверены.
- [Real Tesseract synthetic OCR](pilots/synthetic-ocr/README.md): все 6 known-text slides, 6 created → 6 skipped, оба immutable reports включены в evaluation, 5/6 normalized exact, 0 failed/missing, pack integrity OK. Это не human-reviewed реальный OCR golden set.
- Synthetic scale/performance и fake-tool interruption checks остаются полезными engineering smoke, но не выдаются за live-user performance или crash-safe distributed scheduling.

Следовательно, для checklist выше уже отработаны команды и сохраняемые evidence formats, но реальные входы и judgments для этапов 1–3 всё ещё нужны.

## Метрики

### Extraction и identity

- precision/recall ключевых полей и сущностей;
- ошибочные merges и пропущенные дубли;
- доля claims с рабочим source locator;
- OCR/ASR ошибки, меняющие смысл;
- normalized exact/CER/WER, empty/failed/missing rates и ручная доля смысловых ошибок поверх text-similarity metrics.

### Evidence и research

- unsupported claims на ответ;
- self-citation и circular synthetic support;
- число независимых source families, а не просто ссылок;
- сохранение противоречий;
- freshness на time-sensitive вопросах.

### Польза

- 10 реальных вопросов: правильность, полнота, возможность проверить;
- 3–5 решений: помог ли материал выбрать/отказаться;
- review minutes на элемент и на полезный ответ;
- повторное открытие и повторное использование страниц;
- время и воспроизводимость полной пересборки.

## Таблица результата

Для каждого вопроса и варианта фиксировать одинаковый набор:

```text
question_id | variant | answer_path | correct | supported | review_minutes
source_families | contradictions_preserved | notes
```

Числовая агрегация допустима для сравнения, но не превращается в доменный trust score.

## Критерий остановки

Если B/C не превосходят A достаточно, чтобы окупить сложность и review, сложную wiki не строим. Если ingest или extraction плохи, сначала исправляем их: knowledge compiler не должен маскировать дефектный вход.
