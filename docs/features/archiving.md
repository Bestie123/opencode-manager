# Архивация сессий

> Doc-ID: ARCHIVE-1 | Дата: 2026-06-02 | Связанные: [H-TREE-1], [DB-SELECTOR-1], [REF-DATA-1], [REF-SQL-1]

## 1. Назначение

Архивация позволяет скрыть сессии из основного списка opencode (TUI/Desktop) без физического удаления.
Архивные сессии остаются в БД и могут быть восстановлены в любой момент.

## 2. Когда использовать

- Сессия больше не нужна, но жалко удалять
- Сессия была ошибочной или тестовой
- Хотите скрыть старые сессии из интерфейса opencode
- Подготовка к миграции: сначала архивировать, потом перенести только активные

## 3. Реализация

### 3.1 База данных

```sql
-- Таблица session имеет колонку time_archived (INTEGER, millisecond timestamp)
time_archived INTEGER?  -- NULL = активна, число = timestamp архивации
```

**Значения:**
- `NULL` — сессия активна, отображается в opencode
- `1780381278753` — сессия архивирована, opencode её не показывает

### 3.2 Методы core.py

```python
def archive_session(self, session_id: str) -> bool:
    now = int(time.time() * 1000)
    c.execute("UPDATE session SET time_archived = ? WHERE id = ?", (now, session_id))
    # → SET time_archived = 1780381278753

def unarchive_session(self, session_id: str) -> bool:
    c.execute("UPDATE session SET time_archived = NULL WHERE id = ?", (session_id,))
    # → SET time_archived = NULL
```

### 3.3 SessionInfo

```python
@dataclass
class SessionInfo:
    # ...
    time_archived: Optional[int] = None
    
    @property
    def is_archived(self) -> bool:
        return self.time_archived is not None
```

### 3.4 GUI

- **Колонка «Сост.»** (Status) — между «Дочер.» и «Директория»
  - `🗄` — сессия в архиве
  - пусто — активная
- **Кнопка «Архивировать»** — доступна только при выборе активных сессий
- **Кнопка «Разархивировать»** — доступна только при выборе архивных сессий

### 3.5 Стилизация

```python
# Тёмная тема
self.tree.tag_configure("archived", foreground="#8b949e")
self.tree.tag_configure("archived_subagent", foreground="#6b7280", font=("Consolas", 9, "italic"))

# Светлая тема
self.tree.tag_configure("archived", foreground="#9ca3af")
self.tree.tag_configure("archived_subagent", foreground="#9ca3af", font=("Consolas", 9, "italic"))
```

### 3.6 Приоритет тегов

Приоритет (высший → низший):
1. **Orphan** (красный) — родитель удалён, важнее всего
2. **Subagent + archived** (серый курсив)
3. **Subagent** (серый курсив)
4. **Archived root** (серый)
5. **Default** (обычный)

## 4. Безопасность

Обе кнопки проверяют `_check_opencode()` — блокируются если OpenCode запущен.
```python
def _is_opencode_running(self) -> list[str]:
    # Get-Process -Name 'OpenCode.exe','opencode'
    processes = []
    for name in ["OpenCode", "opencode"]:
        if proc := subprocess.run(...):
            processes.append(name)
    return processes
```

## 5. Сложности

### 5.1 `import time` в core.py
`archive_session()` использует `time.time()`. При первой реализации `time` не был импортирован,
что вызывало `NameError: name 'time' is not defined`.

**Исправление:** добавлен `import time` в начало `core.py`.

### 5.2 Совместимость с opencode Desktop
Desktop-приложение (Tauri) использует sidecar (opencode CLI) для запроса сессий.
SQLite-запрос включает `WHERE time_archived IS NULL`, поэтому архивные сессии
автоматически скрыты в Desktop. Это поведение по умолчанию.

### 5.3 Селектор БД и архивация
Архивация применяется к текущей выбранной БД. Если нужно архивировать сессию
в другой БД — сначала переключитесь на неё через селектор.

## 6. Примеры

### 6.1 Архивировать старые сессии
```sql
-- Все сессии старше 30 дней
UPDATE session SET time_archived = strftime('%s', 'now') * 1000
WHERE time_created < (strftime('%s', 'now') - 30*86400) * 1000
  AND parent_id IS NULL
  AND time_archived IS NULL;
```

### 6.2 Разархивировать все
```sql
UPDATE session SET time_archived = NULL WHERE time_archived IS NOT NULL;
```

## 7. SQL-запросы

```sql
-- Найти все архивные сессии
SELECT id, title, time_archived FROM session WHERE time_archived IS NOT NULL;

-- Подсчитать архивные vs активные
SELECT
  SUM(CASE WHEN time_archived IS NULL THEN 1 ELSE 0 END) AS active,
  SUM(CASE WHEN time_archived IS NOT NULL THEN 1 ELSE 0 END) AS archived
FROM session WHERE parent_id IS NULL;
```
