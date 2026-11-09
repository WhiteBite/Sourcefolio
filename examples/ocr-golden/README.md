# Synthetic OCR golden set

Шесть заранее сгенерированных PNG с известным текстом для воспроизводимой проверки **настоящего Tesseract**, request-level resume, `ocr-evaluate` и evidence-pack integrity.

Это synthetic ground truth, а не human-reviewed корпус реальных TikTok-слайдов. В набор намеренно включены английский и русский текст, мелкий серый текст, идентификатор с underscore и полностью пустой слайд. Неуспешные или пустые случаи не удаляются из evaluation.

## Файлы

- `images/slide-01.png` … `slide-06.png` — исходные изображения;
- `manifest.json` — `capture-manifest/v1` со стабильным source/capture observation;
- `expected.json` — удобный список исходных expected strings;
- `samples.json` — валидный `ocr-evaluation/v1` с content-derived `capture_id`, `artifact_id` и slide locators.

`samples.json` не содержит placeholder IDs: соответствие архивным артефактам проверяется regression test. Повторный `archive-capture` того же manifest создаёт те же ID.

## Запуск

Нужны Tesseract и language data `eng` + `rus`. Путь `TESSDATA` ниже замените на локальный каталог с соответствующими `*.traineddata`.

```bash
BASE=data/pilots/ocr-golden
TESSDATA=/usr/share/tesseract-ocr/5/tessdata
mkdir -p "$BASE/reports"

sourcefolio archive-capture \
  examples/ocr-golden/manifest.json \
  --archive-dir "$BASE/archive"

sourcefolio ocr-backlog \
  --archive-dir "$BASE/archive" \
  --derived-dir "$BASE/derived" \
  --executable /usr/bin/tesseract \
  --tessdata-dir "$TESSDATA" \
  --temp-dir "$BASE/tmp" \
  --language eng+rus --psm 6 --concurrency 2 \
  --capture-limit 1 --slide-limit 6 \
  --report "$BASE/reports/run-001.json"

# Resume: новый immutable report, тот же extractor/config и те же request IDs.
sourcefolio ocr-backlog \
  --archive-dir "$BASE/archive" \
  --derived-dir "$BASE/derived" \
  --executable /usr/bin/tesseract \
  --tessdata-dir "$TESSDATA" \
  --temp-dir "$BASE/tmp" \
  --language eng+rus --psm 6 --concurrency 2 \
  --capture-limit 1 --slide-limit 6 \
  --report "$BASE/reports/run-002.json"

sourcefolio ocr-evaluate examples/ocr-golden/samples.json \
  --derived-dir "$BASE/derived" \
  --batch-report "$BASE/reports/run-001.json" \
  --batch-report "$BASE/reports/run-002.json" \
  --output "$BASE/reports/evaluation.json"

sourcefolio evidence-pack "Sourcefolio" \
  --archive-dir "$BASE/archive" --derived-dir "$BASE/derived" \
  > "$BASE/reports/pack.json"

sourcefolio evidence-pack-verify "$BASE/reports/pack.json" \
  --archive-dir "$BASE/archive" --derived-dir "$BASE/derived" \
  --verify-bytes --output "$BASE/reports/integrity.json"
```

Для следующего независимого запуска используйте новый `BASE` или новые имена reports: существующие report files не перезаписываются.

## Уже сохранённый real-Tesseract результат

См. [pilot README и reports](../../docs/pilots/synthetic-ocr/README.md). В финальном проходе: 6 created, затем 6 skipped; 6 evaluated, 0 failed/missing; normalized exact 5/6, mean CER 0.0042735, mean WER 0.0666667. Ошибка на slide 04 (`cap_ 123456` вместо `cap_123456`) сохранена в report.

Эти цифры характеризуют только простые synthetic PNG и конкретную версию Tesseract. Они не доказывают качество на пользовательских слайдах, смысловую правильность OCR или production throughput/RSS.
