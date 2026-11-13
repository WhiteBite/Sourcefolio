# Реестр кандидатов

Реестр превращает сохранённые captures в per-project список разработок, которые можно пустить в дело: инструменты, библиотеки, репозитории, техники, паттерны, модели, датасеты, темы. Единица учёта — не видео, а кандидат: конкретная вещь, упомянутая в одном или нескольких источниках и пригодная для конкретного проекта. Видео остаётся в архиве как доказательство; реестр отвечает на вопрос «что накоплено по проекту X и что с этим делать».

Контур состоит из четырёх команд и браузерного вида:

```text
registry-extract ──▶ routes (derived) ──▶ registry-build ──▶ snapshot (derived)
                                                                   │
candidate-decide ◀── human notes (vault) ◀── registry-queue ◀──────┘
                                          (views + браузер «Проекты»)
```

Данные живут так:

| Что | Где |
|---|---|
| Маршруты `registry-route/v1` | `data/derived/registry/routes/rt_*.json` |
| Кэш ответов модели | `data/derived/registry-llm/` |
| Снапшоты `registry-snapshot/v1` | `data/derived/registry/snapshots/snap_*.json` + указатель `latest.json` |
| Человеческие решения | `data/vault/candidates/<candidate_id>.md` |
| Generated-заметки `registry-view/v1` | `data/vault/_generated/sourcefolio/registry/` |

Контракты экспортируются через `sourcefolio schema registry-route`, `candidate-record`, `registry-snapshot`, `candidate-decision`.

## 1. Вердикт маршрута: три состояния

`registry-extract` создаёт на каждую пару (источник, проект) один маршрут `registry-route/v1` с вердиктом:

- `candidate` — в тексте есть конкретная разработка; маршрут несёт хотя бы одно заякоренное упоминание.
- `irrelevant` — текст читаем и признан бесполезным для проекта.
- `insufficient_signal` — текст не позволяет судить; обязателен непустой `reason`.

`insufficient_signal` намеренно не свален в `irrelevant`. «Автор не написал ничего, по чему можно судить» и «я прочитал, это не годится» — разные факты: первое — утверждение о входе (пустое описание, нет ни комментариев, ни OCR, элемент недоступен), второе — о содержимом. Если их смешать, отличить шум экстрактора от действительно пустого backlog станет невозможно, а повторный прогон после дозагрузки текста будет выглядеть как «передумали». Элемент с суммарной длиной текстов меньше 40 символов до модели вообще не доходит и получает `insufficient_signal` с reason `not enough text`. Вердикт `candidate`, у которого после валидации не выжил ни один якорь, понижается до `insufficient_signal` (`no verbatim anchor`), а не молча превращается в пустой candidate.

Инварианты контракта: `candidate` несёт хотя бы одно упоминание; `irrelevant` и `insufficient_signal` — ноль.

## 2. Якорь: цитата, span, локатор

Упоминание без якоря не существует. Каждое `CandidateMention` несёт `MentionAnchor`:

- `quote` — дословная цитата из текста источника, 8–500 символов, без парафраза и многоточий;
- `start_char`/`end_char` — символьный span внутри процитированного поля, причём `end_char - start_char == len(quote)`;
- `field` и типизированный локатор: `description`/`title` — без локатора; `comment` — `comment_index` (0-based); `ocr` — `extraction_id`; `caption` — `artifact_id`. Смешивание запрещено валидатором: comment-якорь принимает только `comment_index`, ocr-якорь — только `extraction_id`, и так далее.

Контракт задаёт все пять полей, а текущий экстрактор проверяет цитату посимвольно в тексте `description`, `comment` и `title`. Цитата не найдена, поле вне этого набора, длина вне диапазона, `comment_index` вне списка комментариев — упоминание отбрасывается и учитывается в `dropped_anchors` отчёта. Счётчик не спрятан: на нём построена калибровка (раздел 9).

Поверх модельных упоминаний в маршрут детерминированно добавляются репозиторные: сканирование description и комментариев находит ссылки GitHub/GitLab, и каждая, ещё не покрытая моделью, становится упоминанием с ключом `repo:<host>/<owner>/<repo>` и цитатой, равной самой ссылке с её span. Это страховка, не зависящая от модели: даже если LLM проглядела репозиторий, якорь остаётся.

## 3. Ключи, дедупликация, слияния

Ключи детерминированы, поэтому пересборка не плодит сущности:

- репозиторий — `repo:<host>/<owner>/<repo>` (casefold, без `www.` и суффикса `.git`): один и тот же репозиторий из разных видео даёт один ключ;
- остальное — slug имени (`slugify`, транслитерация кириллицы, обрезка до 200 символов);
- `candidate_id = cand_<sha256(project_id, key)>` — стабилен между пересборками;
- `mention_id = men_<hash>` считается из содержимого упоминания вместе с якорем;
- `route_id = rt_<hash>` считается из `(source_id, capture_id, project_id, model, prompt_fingerprint)`.

Дедупликация автоматическая: `registry-build` группирует упоминания по `(project_id, candidate_key)` в один `Candidate` — kind и title берутся самые частотные, fit — лучший, `mention_ids`/`source_ids` сортируются и уникализируются, `first_capture_at`/`last_capture_at` — границы по маршрутам.

Автоматического слияния «по похожему названию» нет и не будет. Слить два разных ключа в одну сущность — человеческий override через candidate notes: `candidate-decide --candidate-id <источник> --merge-into <цель>` пишет в заметку источника свойство `merged_into`, и следующий `registry-build` переносит упоминания источника в цель, фиксируя у цели `merged_from` (candidate_id источника) и `aliases` (ключ источника). Слияние применяется, только если заметки существуют у обоих участников; слияние в самого себя — ошибка.

## 4. Снапшоты: content-addressed и append-only

`registry-build` собирает из маршрутов, человеческих решений и слияний снапшот `registry-snapshot/v1`. `snapshot_id = snap_<sha256>` считается из содержимого (без `snapshot_id` и `generated_at`), поэтому одинаковый вход даёт одинаковый id. Файл пишется через `write_immutable_bytes`: существующий снапшот не перезаписывается никогда. Новый вход создаёт новый файл рядом, старые не удаляются; указатель `data/derived/registry/latest.json` обновляется атомарно и показывает текущий снапшот. История пересборок остаётся проверяемой.

Повторный прогон не может мутировать принятые записи: решения живут в human notes, билдер их только читает. Accepted-кандидат, исчезнувший из маршрутов (источник переизвлекли, упоминания больше не собираются), не выпадает из реестра — снапшот сохраняет stale-запись (`stale: true`, `mention_count: 0`), и она остаётся видимой со своим outcome. Так выживает только `accepted`: это след человеческого решения, а не шум экстракции.

Счётчики снапшота: `route_count`, `insufficient_signal_routes`, `dropped_claims` (маршруты без вердикта `candidate`); по каждому проекту — `candidate_count`, `accepted`, `rejected`, `stale`.

## 5. Human-зона: candidate notes

Решения живут в human-зоне vault: `data/vault/candidates/<candidate_id>.md`, frontmatter `type: candidate`:

```yaml
type: candidate
candidate_id: cand_<64-hex>
project_id: sourcefolio
key: repo:github.com/owner/repo
kind: repository
title: https://github.com/owner/repo
status: accepted
rationale: беру в пилот OCR
decided_at: 2026-11-13T10:00:00+00:00
approved_by: Mind
approval_at: 2026-11-13T10:00:00+00:00
```

- `status`: `inbox` (по умолчанию) / `accepted` / `rejected` / `deferred`.
- У `inbox` нет ни `decided_at`, ни approval: это «ещё не решено», а не решение. Возврат в `inbox` снимает штампы.
- Любой статус кроме `inbox` требует approval-штампа: `approved_by` плюс `decided_at`/`approval_at`. `candidate-decide` отказывается писать такое решение без `--approved-by`, а доменный валидатор не пропускает решение без человеческого provenance или явного approval.

Почему `approved_by` обязателен: решение — запись плоскости `user_outcome`, а не вывод агента. Без имени утверждающего неотличимо, человек принял кандидата или система сфабриковала auto-accept; со штампом у записи есть автор и момент, и агент не может молча её переписать — смена статуса идёт через ту же команду с новым штампом. Заметка создаётся через `create_if_absent` и обновляется только по frontmatter; тело `## Решение` — человеческая проза, которая не перезаписывается.

## 6. Порядок: только внутри проекта

`registry-queue` упорядочивает кандидатов внутри каждого проекта:

1. outcome: `accepted` → `inbox` → `deferred` → `rejected`;
2. `fit` по убыванию: high → medium → low;
3. `score` по убыванию — максимум pre-rank среди маршрутов упоминаний кандидата;
4. `mention_count` по убыванию;
5. `last_capture_at` по убыванию (свежие раньше);
6. tie-break — `candidate_id`.

`pre_rank` — детерминированная оценка capture, вычисленная ещё до экстракции: ссылка на репозиторий +3, маркеры кода в OCR +2, описание ≥ 40 символов +2, ≥ 3 комментариев +1, likes/plays ≥ 2% +1, нет текста −3, дублирующееся описание −1. Она записывается в маршрут и всплывает в очереди как `score`.

Межпроектного ранжирования нет и не будет: проекты перечислены по `project_id`, у каждого своя очередь. «Самый важный кандидат по всем проектам» — бессмысленное число: fit и outcome определены относительно конкретного проекта, а сравнение через проекты потребовало бы универсального score важности, которого у системы намеренно нет.

## 7. Провенанс

- Маршруты и упоминания — `agent_inference`: provenance маршрута несёт actor_kind `agent`, actor_id — модель, method `chat`. Маршрут `not enough text` — исключение, это `tool`/`metadata`: модель не вызывалась. Generated-заметки очереди несут `provenance: agent_inference` и warning-callout: machine inference, не `creator_claim`.
- Решения — `user_outcome`: actor `user` плюс approval-штамп (`approved_by`, `approval_at`).
- Метаданные репозиториев (stars, license, last push из кэша `repo-catalog` и GitHub API, заметки `entities/`) — `external_observation`.

Плоскости не смешиваются: кандидат в очереди — вывод агента, заякоренный цитатой из источника; фактом для пользователя он становится только после человеческого решения; внешние данные о репозитории живут в своём контуре и не сливаются ни с тем, ни с другим.

## 8. Команды

### registry-extract

```bash
sourcefolio registry-extract \
  --projects data/projects.json \
  --profiles data/vault/projects \
  --collection Нейронки \
  --project sourcefolio \
  --archive-dir data/archive \
  --derived-dir data/derived \
  --limit 25
```

Сканирует latest capture каждого источника (фильтр `--collection`, повторяется), берёт топ `--limit` по pre-rank и для каждого выбранного проекта (`--project`, повторяется; по умолчанию все из `--projects`) запрашивает OpenAI-compatible chat model. В промпт входят имя/описание/профиль проекта (`--profiles`), URL, description, пронумерованные комментарии, OCR (лимит `--max-ocr-kb`, по умолчанию 20) и caption (лимит `--max-caption-kb`, по умолчанию 20). Конфигурация модели резолвится как у `llm-enrich`: `--base-url`/`--api-key`/`--model`, затем env `EV_LLM_BASE_URL`/`EV_LLM_API_KEY`/`EV_LLM_MODEL`, затем `data/llm.json`. Ответы кэшируются в `data/derived/registry-llm/` (ключ `capture_id__project_id__prompt_fingerprint`), до 3 попыток на запрос, не более 3 модельных упоминаний на элемент. Существующий маршрут с тем же `route_id` пропускается; `--refresh` игнорирует и кэш, и сохранённые маршруты. `--dry-run` ничего не пишет и печатает первые два промпта в stderr.

JSON-отчёт: `items_scanned`, `routes_processed`, `skipped_cached`, `failed`, `verdicts`, `mentions`, `dropped_anchors` и per-item `results` (status `processed`/`skipped`/`failed`, verdict, error). Exit code 1 при хотя бы одном failed.

### registry-build

```bash
sourcefolio registry-build \
  --projects data/projects.json \
  --derived-dir data/derived \
  --vault data/vault
```

Читает все маршруты, человеческие решения и слияния из `<vault>/candidates/` и строит снапшот `registry-snapshot/v1`. Отчёт: `snapshot_id`, `snapshot_path`, `candidates`, `mentions`, per-project счётчики (`candidates`/`accepted`/`rejected`/`stale`), `merged`, `stale_accepted`, `dropped`. `--dry-run` считает отчёт и `snapshot_id`, не записывая файл; число различных кандидатов для калибровки видно именно здесь.

### registry-queue

```bash
sourcefolio registry-queue \
  --projects data/projects.json \
  --derived-dir data/derived \
  --vault data/vault \
  --project sourcefolio \
  --status inbox \
  --limit 20 \
  --format markdown
```

Строит очередь из последнего снапшота: `--project` (повторяется) сужает набор проектов, `--status` фильтрует по outcome (`inbox`/`accepted`/`rejected`/`deferred`), `--limit` ограничивает число кандидатов на проект. `--format json` (по умолчанию) печатает очередь JSON'ом (`snapshot_id`, `generated_at`, проекты с кандидатами и упоминаниями), `markdown` — сгенерированные заметки. Без `--dry-run` пишет `registry-view/v1` в `<vault>/_generated/sourcefolio/registry/`: `index.md` плюс `<slug>.md` на проект, не более 3 цитат на кандидата и ссылки на source cards. Без снапшота возвращает пустую очередь.

### candidate-decide

```bash
sourcefolio candidate-decide \
  --candidate-id cand_<64-hex> \
  --status accepted \
  --approved-by Mind \
  --rationale "беру в пилот OCR" \
  --vault data/vault \
  --derived-dir data/derived
```

Записывает человеческое решение: `--status` (`inbox` по умолчанию / `accepted` / `rejected` / `deferred`), `--rationale`, `--approved-by` (обязателен для статусов кроме `inbox`), `--merge-into <candidate_id>` — отметка ручного слияния. Identity (project, key, kind, title) берётся из последнего снапшота или из существующей заметки; неизвестный `--candidate-id` даёт ошибку и exit code 1. Заметка пишется в `<vault>/candidates/<candidate_id>.md`.

### Браузерный вид «Проекты»

`serve` (и `render-ui`) встраивают очередь реестра в локальный браузер: переключатель «Проекты» в верхней панели показывает карточки кандидатов выбранного проекта — kind, fit, число упоминаний, badge outcome и первая цитата. Drawer раскрывает все упоминания (дословные цитаты с переходом «к источнику» в карточку архива) и кнопки решений: Принять / Отложить / Отклонить / В инбокс.

- `GET /api/registry` — JSON очереди.
- `POST /api/candidate` — `{candidate_id, status, rationale?, approved_by?}`; требует заголовок `X-Sourcefolio: 1`, для статусов кроме `inbox` — непустой `approved_by` (браузер берёт имя из localStorage, по умолчанию `user`). Пишет ту же human-заметку, что и `candidate-decide`.

По `file://` после `render-ui` кнопки решений отключены: запись outcome работает только через `serve`.

## 9. Калибровка

Масштабировать экстракцию можно только после замеров на маленьком прогоне:

1. `--dry-run`: посмотреть промпты (первые два печатаются в stderr) и состав элементов.
2. Прогон на 25 элементов: `--limit 25` на одном проекте и коллекции.
3. Решение по отчёту, правило калибровки:
   - `dropped_anchors / mentions ≤ 0.10` — модель цитирует дословно, а не пересказывает;
   - минимум 8 из 25 элементов закончились вердиктом `candidate` хотя бы с одним заякоренным упоминанием;
   - минимум 5 различных кандидатов — их число показывает `registry-build --dry-run`.
4. Только если правило выполнено, снять `--limit` и расширить прогон на остальные коллекции и проекты.

Если правило не выполнено, чинят вход, а не порог: уточняют профиль проекта (`--profiles`), меняют модель (`--model`), повторяют с `--refresh`. Реестр, построенный на шумном экстракторе, хоронит человеческое review под ложными кандидатами.

## 10. Что намеренно не сделано

- Нет ASR: текст `caption` — это субтитровые дорожки, скачанные yt-dlp как артефакты `caption_text`, а не распознавание речи. Такие артефакты есть только у новых captures, скачанных после включения субтитров; у старых metadata-only captures их нет, и в промпт они не попадают. ASR подключается через тот же derived contract и отдельный quality gate.
- Нет embeddings и векторного поиска: ключи, дедупликация и порядок детерминированы и воспроизводимы без модели.
- Нет auto-accept: любой статус кроме `inbox` ставит только человек с явным `approved_by`. Агент предлагает, пользователь решает.
- Нет межпроектного ранжирования: порядок определён только внутри проекта (раздел 6).
- Нет универсального trust score: `fit` — оценка агента для конкретного проекта, а не «надёжность источника».
