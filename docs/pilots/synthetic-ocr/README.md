# Real Tesseract на synthetic golden slides

Дата проверки: **2026-11-03**. Это autonomous synthetic acceptance, не пилот личной коллекции и не human-reviewed OCR benchmark.

## Что проверено

- 6 сохранённых PNG с заранее известным expected text, включая русский, мелкий серый текст, ID с underscore и пустой слайд;
- настоящий **Tesseract 5.5.0**, `eng+rus`, PSM 6, SHA-256 language data входит в request config;
- полный CLI path: `archive-capture` → `ocr-backlog` → resume с новым report → `ocr-evaluate` с обоими reports → `evidence-pack` → `evidence-pack-verify --verify-bytes`;
- неизменность raw SourcePacket и derived records при resume.

Вход и команды: [`examples/ocr-golden/README.md`](../../../examples/ocr-golden/README.md).

## Финальный результат

| Проверка | Результат |
|---|---:|
| Первый OCR run | 6 created, 0 skipped, 0 failed |
| Resume | 0 created, 6 skipped, 0 failed |
| Request IDs первого run и resume | совпали |
| Derived JSON bytes до/после resume | совпали |
| Raw packet до/после OCR/resume/pack | byte-identical |
| Evaluation | 6 evaluated, 0 failed, 0 missing, 0 oversized |
| Normalized exact | **5/6 = 0.833333** |
| Mean CER / WER | **0.0042735 / 0.0666667** |
| Empty actual | 1 — ожидаемый пустой слайд |
| Evidence-pack integrity | `ok=true`, 0 issues |
| Artifact byte hashes | 6/6 проверены |

Wall time финального sandbox run: первый OCR **1.156 s**, resume **0.299 s**, evaluation **0.274 s**. Это единичный маленький run, а не throughput/SLA; item timings и extractor fingerprints находятся в `batch.json` и `resume.json`.

## Все шесть случаев

| Sample | Итог | CER | WER | Комментарий |
|---|---|---:|---:|---|
| slide 01 | exact | 0 | 0 | английский текст |
| slide 02 | exact | 0 | 0 | русский текст |
| slide 03 | exact | 0 | 0 | числа и латиница |
| slide 04 | **не exact** | 0.025641 | 0.4 | OCR вернул `cap_ 123456` вместо `cap_123456` |
| slide 05 | exact | 0 | 0 | мелкий серый текст |
| slide 06 | exact, empty | 0 | 0 | пустой expected/actual; limitation `OCR returned no text` сохранена |

Ошибка slide 04 и empty limitation не отфильтровывались. CER/WER измеряют текстовое сходство после задокументированной normalization, а не смысловую корректность или доверие источнику.

## Сохранённые доказательства

- `batch.json`, `resume.json` — immutable `ocr-backlog-report/v1` для двух запусков;
- `evaluation.json` — `ocr-evaluation-report/v1`, связанный с SHA-256 обоих batch reports;
- `pack.json` — retrieval-only evidence pack, не автоматический ответ;
- `integrity.json` — `evidence-pack-integrity/v1` с фактической проверкой bytes;
- `summary.json` — компактная сводка финального прохода;
- `first-pass/` — результаты первого успешного прохода без отдельного resume report;
- `reporting-orchestration-error/` — сохранённые reports и описание ошибки pilot helper.

Временные archive/derived финального запуска не включены в документацию. Сохранённый pack и integrity report — point-in-time evidence; для новой byte verification выполните воспроизводимые команды из example с новыми report paths.

В промежуточном проходе оба OCR CLI завершились успешно, но helper при подготовке сводки использовал несуществующее поле `locator` вместо `slide` и завершился с `KeyError`. Reports сохранены; evaluation/integrity в том проходе не выполнялись. Финальный проход повторил весь pipeline. Это была ошибка orchestration helper, не Tesseract и не application OCR item.

## Что этот результат не доказывает

- качество OCR на реальных пользовательских TikTok-слайдах;
- human-reviewed semantic precision/recall;
- производительность и RSS на массовом реальном backlog;
- SIGKILL cleanup, distributed leases или crash-safe scheduling;
- качество ASR/keyframes, evidence-backed answers или wiki synthesis.
