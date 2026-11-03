# Synthetic OCR evaluation example

`samples.json` demonstrates the `ocr-evaluation/v1` shape only. Its zero IDs intentionally do not reference a real archive: running it unchanged produces a `missing` result, not an OCR quality measurement.

For a real golden sample:

1. Run `ocr-backlog --report` and `evidence-pack` for the target capture.
2. Copy the real `capture_id`, `artifact_id` and typed `locator` from the pack.
3. Write `expected_text` by human review of the original artifact.
4. Keep `request_id` absent to evaluate the latest matching extractor record, or copy a specific request ID from the pack/batch report to pin one operation.
5. Run `ocr-evaluate` with all relevant `--batch-report` files so explicit failures are not left as `missing`.

CER/WER measure normalized text similarity only. Review meaning-changing OCR errors manually.
