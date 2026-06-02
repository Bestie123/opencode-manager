# Миграция сессий между базами данных

> Doc-ID: GUIDE-MIGRATE-1 | Дата: 2026-06-02 | Связанные: [REF-SQL-1], [DB-SELECTOR-1], [BUGS]

## 1. Зачем это нужно

На системе может быть несколько SQLite-баз OpenCode:

| База | Размер | Сессий | Когда создана |
|------|--------|--------|---------------|
| `opencode.db` | 192 MB | 60 | Стабильная версия |
| `opencode-dev.db` | 0.8 MB | 7 | Dev-сборка из npm |
| `opencode1.db` | 192 MB | 51 | Ручная копия |
| `opencode - копия.db` | 192 MB | 51 | Системная копия |

Основная проблема: CLI и Desktop могут использовать разные БД.
CLI (npm, dev) → `opencode-dev.db`, Desktop (latest) → `opencode.db`.

## 2. Миграция через менеджер

### 2.1 Перенос одной сессии

1. Откройте менеджер
2. Выберите исходную БД в тулбаре (селектор)
3. Найдите нужную сессию
4. Нажмите «Экспорт выбранных» → сохраните JSON
5. Переключитесь на целевую БД
6. Нажмите «Импорт JSON» → выберите сохранённый файл

### 2.2 Перенос всех сессий (пошагово)

1. **Выберите исходную БД** (opencode.db — 60 сессий)
2. **Экспорт всех** → сохраните все JSON в одну папку
3. **Переключитесь на целевую БД** (opencode-dev.db)
4. **Импорт JSON** → выберите все файлы (Ctrl+A)

**Важно:** Импорт создаёт бэкап целевой БД перед записью.

### 2.3 Автоматическое копирование (прямой SQL)

```python
import sqlite3

src = sqlite3.connect(r'~/.local/share/opencode/opencode.db')
dst = sqlite3.connect(r'~/.local/share/opencode/opencode-dev.db')

# Получить список сессий из src
sessions = src.execute("SELECT * FROM session").fetchall()

for s in sessions:
    sid = s[0]
    # Проверить, нет ли уже такой сессии в dst
    exists = dst.execute("SELECT 1 FROM session WHERE id=?", (sid,)).fetchone()
    if exists:
        continue
    
    # Копировать session
    # (полный код — см. mcp-opencode-db.py _check_orphans)
```

## 3. Исправление project_id после миграции

После копирования сессии в новую БД может не совпадать `project_id`.

```sql
-- Узнать правильный project_id для каталога
SELECT id FROM project WHERE worktree = 'Q:\\User_Data\\Desktop\\TestQA';

-- Обновить сессии
UPDATE session SET project_id = 'a8a2d42272aeac95b2502345313a1f1866da532a'
WHERE directory LIKE '%TestQA%' AND project_id = 'global';
```

## 4. Миграция с Desktop на CLI (и обратно)

Desktop-приложение (v1.15.13) использует **opencode.db**.
CLI из npm (dev) использует **opencode-dev.db**.

Чтобы сессии были видны в обоих — нужно скопировать их в целевую БД.

### 4.1 Копирование одной сессии через MCP

```bash
# Внутри opencode-сессии:
# используй oc_get_session db=opencode.db session_id=ses_...
# затем oc_query db=opencode-dev.db sql=INSERT INTO session ...
```
(Внимание: MCP-инструменты read-only, для записи используйте прямой SQL через bash.)

### 4.2 Полная синхронизация

Если нужно синхронизировать обе БД полностью:
```bash
# Через менеджер: экспорт из одной, импорт в другую.
# Или через прямой SQL-запрос с Python (см. выше).
```

## 5. После миграции

1. Перезапустите opencode (Desktop или CLI)
2. Проверьте что сессии отображаются
3. Удалите старые сессии из исходной БД (если нужно)

## 6. Известные проблемы

- **Разные project_id** для одного каталога — исправляется UPDATE
- **Archived сессии** — после миграции нужно разархивировать
- **Session_diff** — не копируется при экспорте/импорте JSON (только БД)
- **Orphan-дети** — если родитель не скопирован, дети станут orphan'ами
