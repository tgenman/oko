# Упаковка, staged generation и CI

Status: done
Blocked by: 01
Assignee: codex

Спецификация: [spec.md](../spec.md).

## Приёмка

Wheel сохраняет API/assets и freshness gates; staged generation проверяет новые группы и lock; CI использует lock.

## Comments

2026-10-15. База реализации и ревью: `2755194342be1ed87788db0534156314897608c4`. Незавершённый перенос браузерных тестов на TypeScript остаётся в основном checkout и в эту задачу не входит.

TDD: прежний wheel воспроизвёл extra dev; после переноса в группу проверка прошла. Staged API governance сначала падал на старом формате dev, после адаптации прошёл. Целевые наборы: 6 passed, 95.52 s. CI переведён на закреплённый Poetry и lock.
