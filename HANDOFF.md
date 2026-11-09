# Перенос Sourcefolio на другой компьютер

Архив содержит полный исходный проект, тесты, JSON Schema contracts, synthetic examples, OCR golden PNG, документацию и сохранённые pilot-reports. Он не содержит `.venv`, caches, временные archives, личный TikTok export или скачанные public media.

## 1. Требования

- Python **3.12+** (рекомендуется 3.12 или 3.13).
- Доступ в интернет для первой установки Python-зависимостей.
- Git не нужен.
- Для реального OCR дополнительно нужен Tesseract 5 с language data `eng` и `rus`.
- Для широкой совместимости видео форматов желателен FFmpeg. Он не нужен для unit tests.

Не переносите старую `.venv`: она зависит от ОС и абсолютных путей. Создайте новую в распакованном каталоге.

## 2. Windows PowerShell

Откройте PowerShell в каталоге `sourcefolio` внутри распакованного архива:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev,media,slides]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\sourcefolio.exe --help
```

Если `py -3.12` не находит интерпретатор, установите Python 3.12/3.13 и используйте соответствующий аргумент `py -3.12` или `py -3.13`.

Полная проверка качества:

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy src
.\.venv\Scripts\python.exe -m pytest
```

Ожидаемый baseline архива: **125 tests passed**. Live TikTok и настоящий Tesseract из test suite не вызываются.

## 3. Linux / macOS

```bash
cd sourcefolio
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev,media,slides]'
.venv/bin/python -m ruff format --check .
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
.venv/bin/python -m pytest
.venv/bin/sourcefolio --help
```

При необходимости замените `python3.12` на `python3.13`.

## 4. Версии live media tools

`pyproject.toml` разрешает обновления `yt-dlp` и `gallery-dl`. Чтобы воспроизвести версии сохранённого public surrogate run, после основной установки выполните:

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-pilot.txt
```

Linux / macOS:

```bash
.venv/bin/python -m pip install -r requirements-pilot.txt
```

Это необязательно для разработки и unit tests. Tesseract является системной зависимостью и в pip requirements не входит.

## 5. Откуда продолжить с личным TikTok export

1. Положите реальный export в новый локальный каталог, например `data/imports/user_data.json`. Каталог `data/` намеренно исключён из архива и `.gitignore`.
2. Сначала только разберите export без массового скачивания:

```bash
sourcefolio tiktok-inspect data/imports/user_data.json \
  --export-timezone Europe/Moscow

sourcefolio tiktok-import data/imports/user_data.json \
  --export-timezone Europe/Moscow \
  --concurrency 4
```

На Windows без активации venv используйте `.\.venv\Scripts\sourcefolio.exe` вместо `sourcefolio`. На Linux/macOS без активации — `.venv/bin/sourcefolio`.

3. Проверьте `data/archive` и `data/control`, затем начните с маленького live limit:

```bash
sourcefolio tiktok-download data/imports/user_data.json --limit 10
sourcefolio tiktok-slides data/imports/user_data.json --limit 10
sourcefolio capture-report --format markdown
sourcefolio archive-audit --verify-bytes
```

4. Следуйте `docs/PILOT.md`: сохраняйте failure mix, повторяйте operations для resume и не начинайте массовый backfill до проверки representative cases.

## 6. Полезные материалы

- `README.md` — все реализованные команды и текущие границы.
- `docs/PILOT.md` — пользовательский acceptance checklist.
- `docs/ROADMAP.md` — что уже сделано и какие gates остаются.
- `docs/PERFORMANCE.md` — synthetic scale и real-tool measurements.
- `docs/pilots/public-ingest/` — sanitized live public video/photo results.
- `docs/pilots/synthetic-ocr/` — real-Tesseract batch/resume/evaluation/integrity reports.
- `examples/ocr-golden/README.md` — воспроизведение OCR pipeline на своей машине.

Сохранённые pilot-reports являются историческими результатами sandbox. В них нет переносимого SQLite control state или медиаархива; для личного пилота создайте новый `data/`. Не помещайте cookies, tokens или приватный export в каталог, который планируете снова архивировать для передачи.
