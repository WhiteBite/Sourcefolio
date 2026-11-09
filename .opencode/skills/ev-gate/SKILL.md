---
name: ev-gate
description: Прогон полного quality gate проекта Sourcefolio (ruff format/check, mypy strict, pytest) и триаж падений на Windows. Use when нужно проверить репозиторий после правок, "прогони тесты", "гейт", "проверь перед коммитом", CI красный, тесты падают на Windows, mypy ругается на killpg.
---

# Sourcefolio: gate и триаж

## Gate (те же шаги, что CI)

Команды из корня репо. Windows: `.\.venv\Scripts\python.exe`, Linux/macOS: `.venv/bin/python`. Если venv нет — `python -m venv .venv` и `pip install -e ".[dev,media,slides]"`.

```powershell
.\.venv\Scripts\python.exe -m ruff format --check .   # все файлы formatted
.\.venv\Scripts\python.exe -m ruff check .            # All checks passed
.\.venv\Scripts\python.exe -m mypy src                # Success: no issues in 50 source files
.\.venv\Scripts\python.exe -m pytest                  # 125 passed
```

Гейт либо зелёный целиком, либо правка не закончена. Частичный прогон — только для локальной отладки; финальное слово за полным набором. Число тестов не уменьшать: baseline 125 растёт только новыми тестами.

## Триаж падений на Windows

| Симптом | Причина | Фикс |
|---|---|---|
| `WinError 193` при старте fake executable | тест пишет shebang-скрипт напрямую | создавать только через `write_fake_executable` из `tests/conftest.py` |
| `ZoneInfoNotFoundError: Europe/Moscow` (или UTC) | на win32 нет системного tzdata | `pip install -e ".[dev]"` заново; в pyproject уже есть маркер `tzdata; sys_platform == "win32"` |
| `PermissionError [WinError 32]` на temp cleanup в timeout-тесте | process.kill() убил cmd.exe, а python-внук жив и держит хэндл лога | kill дерева `taskkill /F /T /PID` в `_kill_process` адаптера; не "чинить" повторным kill |
| mojibake в assert'ах (`?????` вместо текста) | cp1251 в пайпах/файле | fake обязан начинаться с `sys.stdout.reconfigure(encoding="utf-8")`; в тестах `read_text(encoding="utf-8")` |
| mypy `Module has no attribute "killpg"/"SIGKILL"` | typeshed помечает их non-win32 | платформенное сужение только `sys.platform != "win32"`, не `os.name` |

## Красные линии

- `pytest` не ходит в сеть: живой TikTok и настоящий Tesseract запрещены.
- Падение, не связанное с текущей правкой, — зафиксировать и сообщить, не чинить молча.
- После фикса — полный повторный прогон gate, не только упавшего теста.
