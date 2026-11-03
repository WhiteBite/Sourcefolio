# Synthetic TikTok export

Это полностью synthetic fixture для проверки parser и CLI. ID и short link не описывают реальные ролики.

Использована публично встречающаяся форма `Activity → Favorite Videos → FavoriteVideoList` с `Date` и `Link`. Поля `Collection`, `Collections` и `SyntheticExtraField` добавлены специально для тестирования расширяемости: официальный export может их не содержать. Парсер сохраняет такие поля в raw entries, но не обещает, что TikTok экспортирует названия коллекций.

Naive dates намеренно оставлены без автоматического UTC. Для их нормализации передайте `--export-timezone Europe/Moscow` или фактический часовой пояс export.
