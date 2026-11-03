# ADR 0006: Rebuildable generated views и измеряемый lexical baseline

- Дата: 2026-11-03
- Статус: accepted

## Контекст

После появления ingestion нужен полезный интерфейс к архиву, но преждевременный vector/GraphRAG слой создал бы новую инфраструктуру до измерения реальных вопросов. Obsidian также нельзя превращать в control plane или позволять renderer перезаписывать человеческие заметки.

## Решение

1. Canonical `SourcePacket` остаётся общим входом для Obsidian projection и search.
2. Source cards полностью пересобираемы и живут только в dedicated generated namespace. Rebuild использует staging и не адресует human-owned paths.
3. Первый search — deterministic Unicode lexical AND с title/collection weighting и явными filters. Score означает только query relevance.
4. Binary media не индексируется. Каждый textual artifact читается с byte limit.
5. Текущий CLI допускает in-memory index и O(N) query. Это baseline для измерения, не финальная search architecture.

## Последствия

- Cards можно удалить и восстановить без потери знаний; человеческие заметки остаются вне generated root.
- Нет новых сервисов, embeddings, model keys или background daemon.
- Поиск не понимает синонимы/семантику и не выполняет OCR/ASR; это ограничение фиксируется в документации.
- При неприемлемых latency/RSS/quality появляется persistent FTS/hybrid adapter за портом, без смены evidence IDs.

## Gate к усложнению

Записать реальные запросы, latency, RSS, найденные/пропущенные релевантные источники и время review. Vector/GraphRAG или wiki-compiler принимаются только при измеримом преимуществе над этим baseline.
