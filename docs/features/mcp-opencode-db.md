# MCP-инспектор баз opencode

> Doc-ID: MCP-OCDB-1 | Дата: 2026-06-02 | Связанные: [ARCH-v2], [DB-SELECTOR-1]

## Назначение

MCP-сервер для прямого доступа к SQLite-базам opencode изнутри opencode-сессии.
Позволяет AI-агенту инспектировать структуру БД, находить orphan-сессии, сравнивать базы.

## Файл

`Q:\User_Data\Desktop\opencode-manager\mcp-opencode-db.py`

## Протокол

MCP stdio — стандартный протокол opencode для локальных инструментов.
Инициализация: при старте opencode (MCP грузятся один раз).

## Инструменты

| Инструмент | Параметры | Описание |
|---|---|---|
| `oc_list_dbs` | — | Список всех `opencode*.db` (путь, имя, сессий, размер) |
| `oc_list_sessions` | `db`, `project_id?`, `parent_id?`, `search?`, `limit?` | Сессии с фильтрами, колонки: id, title, project_id, parent_id, tokens, + число детей |
| `oc_get_session` | `db`, `session_id` | Полная сессия: метаданные, сообщения, parts |
| `oc_get_children` | `db`, `parent_id`, `recursive?` | Дочерние сессии рекурсивно (до глубины 10) |
| `oc_check_orphans` | — | Сравнение `opencode.db` vs `opencode-dev.db`: orphan-дети, project_id mismatch, перекрёстный анализ |
| `oc_query` | `db`, `sql` | Read-only SQL (только SELECT) |

## Подключение

Добавлено в `C:\Users\misch\.config\opencode\opencode.jsonc`:

```json
"mcp-opencode-db": {
  "type": "local",
  "command": ["C:\\Users\\misch\\AppData\\Local\\Programs\\Python\\Python313\\python.exe", "Q:\\User_Data\\Desktop\\opencode-manager\\mcp-opencode-db.py"],
  "enabled": true
}
```

**Важно:** 
- MCP грузятся при старте opencode. Изменения конфига применяются только после перезапуска.
- На Windows нужно указывать ПОЛНЫЙ путь к python.exe. `"python"` не работает — opencode не видит PATH так же как терминал.

## Зависимости

- Python 3.10+
- `mcp` (pip install mcp) — уже установлен глобально

## Сложности

1. **MCP in-session:** MCP нельзя добавить/перезагрузить без перезапуска opencode.
   Альтернативы не было — это ограничение протокола.
2. **Read-only:** Все инструменты только читают БД. Запись осознанно не реализована — риск повреждения.
3. **Блокировка:** Если Desktop-приложение держит WAL-блокировку, запрос может упасть по таймауту (3 сек).
