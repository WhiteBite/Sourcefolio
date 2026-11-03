# Исследование инструментов и референсов

Это сохранённые выводы двух аналитических проходов. Статус молодых проектов и надёжность TikTok-доступа быстро меняются; перед выбором адаптера нужно перепроверить репозиторий и провести пилот. Ни один проект ниже не принят автоматически как зависимость.

## TikTok ingestion

| Инструмент / источник | Полезная роль | Ограничение / проверка |
|---|---|---|
| [Официальный TikTok data export](https://support.tiktok.com/en/account-and-privacy/personalized-ads-and-data/requesting-your-data) | Первичный список сохранений и аккаунтных данных | Не готовый медиаархив; архив доступен ограниченное время; свежесть и наличие коллекций проверить на реальном export |
| [Stashr об export](https://stashr.me/blog/export-tiktok-data) | Предупреждение о практических ограничениях | Favorites могут оказаться только date + URL без collection names |
| [Rot2Gold](https://github.com/MarcoSburlino/rot2gold) | Близкий референс: collections, video/slides, OCR/ASR, Obsidian | Молодой проект; captions/hashtags/comments не закрыты; тестировать, а не считать зрелым лидером |
| [Sidequest / saved-video-intelligence](https://github.com/mattthomas7641/saved-video-intelligence) | Research до первоисточников, GitHub check, project-fit, actions | Не Obsidian-centric готовая основа |
| [Dewey TikTok guide](https://getdewey.co/how-to-use/tiktok-favorites-collections/) | Потенциально простой sync Favorites + Collections и backup | Заявления extension проверять на аккаунте; guide обновлялся 22.01.2025 |
| [yt-dlp issue #17865](https://github.com/yt-dlp/yt-dlp/issues/17865) | Ограничение baseline downloader | Один yt-dlp не закрывает TikTok image slideshow |
| [gallery-dl issue #6878](https://github.com/mikf/gallery-dl/issues/6878) | Проверка отдельного slideshow/media path | Нужен resolver и явная failure queue |

Для реализованного slideshow resolver дополнительно проверены [официальные CLI options gallery-dl](https://gdl-org.github.io/docs/options.html) (`--config-ignore`, `--no-input`, retries/timeout/range/filesize limits, write-metadata) и [актуальный TikTok extractor](https://github.com/mikf/gallery-dl/blob/master/gallery_dl/extractor/tiktok.py): photo items получают `num`, а исходный inventory находится в `imagePost.images`. Adapter использует эти поля для order/completeness checks, но не архивирует вложенные signed image/audio URLs. Live compatibility всё равно остаётся pilot gate.

Дополнительно рассматривались `rShaw5/tiktok-processor` (фактически недоделан) и `kevin-mead/Collections-Scraper` (узкий ingestion-референс, не готовая система).

Для parser v1 дополнительно проверены публичные примеры структуры: [TikTock](https://github.com/Izaan17/TikTock) показывает `Activity → Favorite Videos → FavoriteVideoList` с `Date`/`Link`; [Tiktok-dl](https://github.com/dhillrigo/Tiktok-dl) описывает JSON/TXT parsing и resumable state; [пример export](https://gist.github.com/extratone/c6c81f022721ee9f5a85bbdc28136661) подтверждает встречающиеся `tiktokv.com/share/video/<id>` links. Это ориентиры для tolerant adapter, не гарантия схемы конкретного аккаунта. Реальный export пользователя остаётся acceptance test.

## Knowledge / provenance

| Проект | Что взять | Чего не делать |
|---|---|---|
| [Karpathy LLM Wiki](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) | immutable sources, Markdown wiki, schema, ingest/query/lint | Не превращать каждый saved item в обязательную статью |
| [claude-obsidian](https://github.com/AgriciDaniel/claude-obsidian) / [WIKI](https://github.com/AgriciDaniel/claude-obsidian/blob/main/WIKI.md) | content-addressed raw, source/claim ledgers, recoverable transactions, freshness, строгие accepted-claim правила | Требует внешних media/OCR; кандидат B, не автоматически ядро |
| [llm-wiki-compiler](https://github.com/atomicstrata/llm-wiki-compiler) | ingest/compile/review/lint/eval/context, profiles, OKF import/export | Не TikTok ingestion; не запускать как high-churn firehose без selective compile |
| [nvk/llm-wiki](https://github.com/nvk/llm-wiki) | thesis/question как фильтр knowledge bloat | Не навязывать одну глобальную thesis всем личным коллекциям |
| [Personal-Ontology](https://github.com/haorantang97/Personal-Ontology) | proposal-gated writes, independent source families | Не считать количество повторов независимым подтверждением |
| [OKF spec v0.2](https://github.com/GoogleCloudPlatform/knowledge-catalog/blob/main/okf/SPEC.md) | Markdown+YAML bundle, provenance, generated/verified, lifecycle, freshness, attestation | Экспортный профиль, не обязательная внутренняя schema |
| [Google Cloud об OKF trust signals](https://cloud.google.com/blog/products/data-analytics/okf-v0-2-adds-trust-signals) | Объективные credibility signals вместо универсального score | Доверие определяется потребителем и контекстом |

## Capture / research / search

- [Karakeep](https://github.com/karakeep-app/karakeep) — возможный capture/bookmark компонент, не каноническая основа.
- [Open Notebook](https://github.com/lfnovo/open-notebook) и [SurfSense](https://github.com/MODSetter/SurfSense) — research/chat-компоненты для сравнения, не повод дублировать canonical store.
- [Obsidian Bases](https://help.obsidian.md/bases) — views над Markdown properties.
- [Obsidian Web Clipper](https://help.obsidian.md/web-clipper) — будущий web capture, не TikTok archive.
- [QMD](https://github.com/tobi/qmd) — локальный hybrid search с MCP; подключать после измеренной потребности.
- [Repomix](https://github.com/yamadashy/repomix), [Gitingest](https://github.com/coderamp-labs/gitingest), [DeepWiki](https://cognition.com/blog/deepwiki) — глубокий разбор выбранных репозиториев, не массовая обработка каждого упоминания.

## Архитектурный вывод

Не существует одного проверенного инструмента, который одновременно закрывает TikTok collections, надёжный media archive, evidence/provenance, личный контекст, решения и Obsidian. Поэтому стратегия:

**Own the contract, rent the implementations.**

Пользователю принадлежат:

- source/capture/artifact identity;
- исходные байты и SourcePackets;
- collection memberships и ручные entity merges;
- evidence и source families;
- personal context, decisions и outcomes;
- golden set, acceptance tests и экспорт.

Заменяемыми остаются downloaders, OCR/ASR, LLM, search, queue и wiki engines.
