# project-fit scale smoke

Дата проверки: **2026-11-09**. Проверка ограниченного (`--limit`) прогона `project-fit` на реальном локальном архиве коллекций. Живая chat model вызывалась только в bounded режиме; массовый прогон коллекций не выполнялся.

## Конфигурация

- Модель: `deepseek-ai/DeepSeek-V4.1-Flash` (OpenAI-compatible chat), конфигурация из `data/llm.json` — API-ключ в репозиторий не входит.
- Коллекции в архиве: «Нейронки» — 503 элемента, «Фильмы» — 581 элемент (плюс ещё 22 коллекции).
- Проекты: `data/projects.json` (`midas`/MidasAI, `mafiin`/Mafiin).

## Результаты

| Запуск | Команда | Результат | Записи |
|---|---|---|---|
| План | `project-fit --collection Нейронки --dry-run --limit 3` | `items_scanned=3`, `dry_run=true`, `failed=0` | нет (dry-run ничего не пишет) |
| Один элемент | `project-fit --collection Нейронки --limit 1 --refresh` | `processed=1`, `failed=0` | `projects/midasai.md`, `projects/mafiin.md`, `projects/index.md` |

Dry-run подтвердил, что план и промпты строятся без вызовов модели и без записи. Bounded real run подтвердил, что живой endpoint отвечает, кэш и индекс совпадений обновляются, а per-project briefs `project-fit/v1` пересобираются.

## Что это не доказывает

- массовый прогон 503/581 элементов: throughput, стоимость и failure rate на полном объёме не измерены — это операция пользователя, не CI;
- качество сопоставления (нужен human-reviewed набор, а не smoke);
- живые rate-limit/timeout случаи на длинном прогоне.

Результат point-in-time: повторный запуск даёт новое наблюдение, а `project-fit/v1` остаётся rebuildable view с `provenance: agent_inference`.

## Файлы

Живые артефакты остаются в gitignored `data/`: `data/derived/project-fit/index.json` и `data/vault/_generated/sourcefolio/projects/`.
