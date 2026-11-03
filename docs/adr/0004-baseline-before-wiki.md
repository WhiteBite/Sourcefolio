# ADR 0004: Evidence baseline до выбора wiki engine

- Статус: accepted
- Дата: 2026-11-03

## Контекст

Сложная wiki может создать впечатление пользы за счёт большого числа красивых заметок, при этом ухудшая provenance и время review. Ingest и extraction ошибки нельзя исправить компилятором знаний.

## Решение

Сначала общие source packets и baseline A: source cards + search + on-demand answers. Затем на одном golden set сравнить `claude-obsidian` и `llm-wiki-compiler`. Доверие не сводится к одному score; creator claims, agent inferences, external observations и user outcomes различаются типами.

## Последствия

Если baseline достаточен, система остаётся простой. Wiki engine принимается только по измеренному выигрышу на реальных вопросах и решениях.
