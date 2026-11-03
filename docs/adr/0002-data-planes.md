# ADR 0002: Разделить archive, vault, control и views

- Статус: accepted
- Дата: 2026-11-03

## Контекст

Obsidian удобен человеку, но не должен быть очередью, lock-manager, медиа-хранилищем и единственным источником машинных состояний. Generated synthesis и human-owned decisions имеют разные правила записи.

## Решение

Четыре плоскости: immutable evidence archive; human-owned knowledge vault; operational control plane; rebuildable views/exports. Каждая имеет собственную каноничность и lifecycle.

## Последствия

Появляется необходимость явных ссылок и projections, зато повторная обработка не затирает ручные знания, backlog не засоряет vault, а систему можно восстановить из owned data.
