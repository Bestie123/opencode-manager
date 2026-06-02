# MCP-инспектор баз opencode

> Doc-ID: MCP-OCDB-1 | Дата: 2026-06-02 | Связанные: [ARCH-v2], [DB-SELECTOR-1], [REF-SQL-1], [GUIDE-TROUBLE-1]

## 1. Назначение

MCP-сервер для прямого доступа к SQLite-базам opencode изнутри opencode-сессии.
Позволяет AI-агенту (мне) инспектировать структуру БД, находить orphan-сессии,
сравнивать базы, выполнять read-only SQL-запросы.

## 2. Файл

`Q:\User_Data\Desktop\opencode-manager\mcp-opencode-db.py`

## 3. Протокол

MCP stdio — стандартный протокол opencode для локальных инструментов.
Инициализация: при старте opencode (MCP грузятся один раз).

## 4. Инструменты

### 4.1 `oc_list_dbs`
**Параметры:** нет
**Возвращает:** список всех `opencode*.db` (путь, имя, сессий, размер)
**Пример ответа:**
```json
[
  {"path": "C:\\Users\\.local\\share\\opencode\\opencode.db", "sessions": 60, "size": 192495616}
]
```

### 4.2 `oc_list_sessions`
**Параметры:** `db`, `project_id?`, `parent_id?`, `search?`, `limit?`
**Фильтры:**
- `project_id` — точное совпадение
- `parent_id` — `"null"` для корневых, `"any"` для детей, `"<ID>"` для конкретного родителя
- `search` — LIKE поиск по title и ID

### 4.3 `oc_get_session`
**Параметры:** `db`, `session_id`
**Возвращает:** полную сессию со всеми сообщениями и частями (parts)
**Внимание:** сессии с 1000+ сообщений могут вернуть МЕГАБАЙТЫ данных.

### 4.4 `oc_get_children`
**Параметры:** `db`, `parent_id`, `recursive?`
**Возвращает:** дочерние сессии рекурсивно (до глубины 10)
**Пример:** родитель с 5 subagent → каждая subagent может иметь свои subagent

### 4.5 `oc_check_orphans`
**Параметры:** нет
**Возвращает:** сравнение `opencode.db` vs `opencode-dev.db`
- `comparison.opencode.db` — статистика первой БД
- `comparison.opencode-dev.db` — статистика второй
- `cross_db.only_in_opencode_db` — список сессий только в первой
- `cross_db.project_id_mismatches` — сессии, у которых project_id различается между БД
- `cross_db.orphan_children` — дети без родителя

### 4.6 `oc_query`
**Параметры:** `db`, `sql`
**Ограничение:** только SELECT. Попытка INSERT/UPDATE/DELETE вернёт ошибку.

## 5. Подключение

Добавлено в `C:\Users\misch\.config\opencode\opencode.jsonc`:

```json
"mcp-opencode-db": {
  "type": "local",
  "command": ["C:\\Users\\...\\python.exe", "Q:\\...\\mcp-opencode-db.py"],
  "enabled": true
}
```

**Важно:**
- На Windows нужно указывать ПОЛНЫЙ путь к `python.exe`. `"python"` не работает.
- После изменения конфига нужен перезапуск opencode.
- MCP грузятся один раз при старте — нет команды «перезагрузить MCP».

## 6. Зависимости

```bash
pip install mcp
```

Проверка: `python -c "import mcp; print('OK')"`

## 7. Сложности

### 7.1 MCP in-session
MCP нельзя добавить/перезагрузить без перезапуска opencode.
Это ограничение протокола — все MCP инициализируются при старте.

### 7.2 Read-only
Все инструменты только читают БД. Запись осознанно не реализована —
риск повреждения данных слишком высок.

### 7.3 Блокировка WAL
Если Desktop-приложение держит WAL-блокировку, запрос может упасть
по таймауту (3 сек). **Решение:** закрыть Desktop или подождать.

### 7.4 PowerShell на Windows
`subprocess.run("opencode")` из Python падает на Windows — opencode это .ps1 скрипт.
**Решение:** вызывать через `powershell -NoProfile -Command "opencode db path"`.

### 7.5 BOM в global.dat
При записи JSON с `encoding='utf-8-sig'` добавляется UTF-8 BOM,
который Tauri Store не понимает. **Решение:** всегда использовать `encoding='utf-8'`.

## 8. Диагностика

Если MCP не загружается:
```bash
# Проверить, видит ли opencode конфиг:
opencode debug config | grep -A5 mcp-opencode-db

# Проверить, установлен ли пакет mcp:
python -c "import mcp; print('OK')"

# Проверить, открывается ли файл:
python Q:\...\mcp-opencode-db.py
```
