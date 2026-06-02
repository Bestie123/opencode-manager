# Архивация сессий

> Doc-ID: ARCHIVE-1 | Дата: 2026-06-02 | Связанные: [H-TREE-1], [DB-SELECTOR-1]

## Назначение

Позволяет скрыть сессии из основного списка без физического удаления.
Архивные сессии не отображаются в opencode (TUI/Desktop), но остаются в БД.

## Реализация

### База данных (`core.py`)

Таблица `session` имеет колонку `time_archived` (INTEGER, timestamp ms).
- `NULL` — активная сессия
- число — timestamp архивации

**Методы:**
- `archive_session(session_id)` — `UPDATE session SET time_archived = ? WHERE id = ?`
- `unarchive_session(session_id)` — `UPDATE session SET time_archived = NULL WHERE id = ?`

**SessionInfo:**
- Поле `time_archived: Optional[int]`
- Property `is_archived` = `self.time_archived is not None`

### GUI (`app.py`)

- **Колонка «Сост.»** — между «Дочер.» и «Директория». Показывает `🗄` для архивных, пусто для активных.
- **Кнопка «Архивировать»** — доступна только когда выбраны активные сессии.
- **Кнопка «Разархивировать»** — доступна только когда выбраны архивные сессии.
- **Защита:** обе кнопки проверяют `_check_opencode()`.
- **Стилизация:** архивные строки — серый текст (теги `archived`, `archived_subagent`).

### Сложности

1. **time import:** `archive_session()` использует `time.time()`. В `core.py` не было импорта `time`. Исправлено добавлением `import time` в начало файла.
2. **Приоритет тегов:** если сессия и архивная, и orphan — показывается красный orphan-тег (важнее).
3. **opencode совместимость:** `time_archived IS NOT NULL` — opencode TUI/Desktop не показывают такие сессии. Это поведение по умолчанию.
