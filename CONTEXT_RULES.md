# opencode-manager — Context Rules

> Doc-ID: CM-PROJECT | Дата: 2026-06-02 | Связанные: [ARCH-v2], [DB-SELECTOR-1], [H-TREE-1], [S-MOVE-2], [MCP-OCDB-1]

## Project
GUI-инструмент для управления сессиями OpenCode через SQLite-базу.

## Stack
- Python 3.10+ (только stdlib: tkinter, sqlite3, json, threading)
- `mcp` (pip install mcp) — для MCP-сервера mcp-opencode-db.py
- Без внешних зависимостей (кроме опционального mcp)

## Code structure
- `app.py` — GUI на tkinter (класс App, 5 вкладок + меню + статус-бар)
- `core.py` — работа с БД (OpenCodeDB: чтение/запись opencode.db, OpenCodeCLI: экспорт/импорт)
- `run.py` — точка входа
- `start.bat` / `start.ps1` — лаунчеры
- `mcp-opencode-db.py` — MCP-сервер для инспекции БД
- `docs/` — документация с Doc-ID

## Key classes
- `App(tk.Tk)` — главное окно, все вкладки и методы
- `OpenCodeDB` — все запросы к opencode.db (+ `list_databases()`, `_detect_db_path()`)
- `OpenCodeCLI` — вызов opencode CLI для экспорта/импорта
- `SessionInfo` — dataclass с полями сессии (+ parent_id, is_subagent)
- `DBStats` — dataclass со статистикой

## Theme
- `_toggle_theme()` — переключение тёмной/светлой темы
- Цвета задаются через `style.configure()` (ttk) и `.configure()` (tk-виджеты)
- **Обе ветки (dark/light) должны явно задавать ОДИНАКОВЫЙ набор свойств** — иначе при переключении остаются значения от предыдущей темы
- `style.theme_use("clam")` — только в `_setup_styles()`, не при каждом переключении
- Сохранение: `~/.opencode-manager/config.json` (`dark_mode: bool`)

## DB (readonly)
- `~/.local/share/opencode/opencode.db` — SQLite (60 сессий)
- `~/.local/share/opencode/opencode-dev.db` — SQLite (7 сессий)
- Таблицы: session, message, part, project, todo, session_share, event, workspace
- session: `parent_id` — NULL для корневых, ID родителя для subagent
- message: `data` (JSON) содержит `role`, `summary.diffs` и т.д.
- part: `data` (JSON) содержит `type` (text/tool/reasoning/patch/step-start/step-finish/compaction/file)
- project: `id` = git-хэш корня, `worktree` = путь к корню

## Key methods in app.py
- `_load_sessions()` — загрузка списка сессий в потоке
- `_refresh_tree()` — иерархическая вставка (родитель → subagent)
- `_refresh_messages()` — загрузка сообщений сессии с lazy-loading
- `_show_chat_message(msg)` — отображение диалога в чат-формате
- `_delete_selected_messages()` — удаление сообщений (удаляет и из part, и из message)
- `_update_session_counters()` — пересчёт токенов в сессии (ВСЕГДА коммитит)
- `_toggle_theme()` — переключение темы
- `_change_session_directory()` — перенос сессии (обновляет directory, path, project_id)

## Filters
- `get_chat_messages(has_parts_only=True)` — скрыть компактированные сообщения
- `get_chat_messages_count(has_parts_only=True)` — количество с фильтром

## Lazy loading (Messages tab)
- По 500 записей, подгрузка при прокрутке >85%
- Сортировка: дата — перезапрос из БД, тип/статус/размер — in-memory

## Known issues
- Нет функции бэкапа всей БД из интерфейса
- `opencode session list` не работает в dev-сборке 0.0.0-dev-202606012329
- 5 orphan-детей в opencode.db (родители удалены)

## Move session to project
- `core.py`: `update_session_directory(session_id, new_directory)` — UPDATE `directory`, `path`, `project_id`
- `core.py`: `_resolve_project_id()` — определяет project_id по git-корню
- `app.py`: `_change_session_directory()` — кнопка «Перенести в проект» на вкладке «Сессии»
- Subagent-сессии блокированы: кнопка disabled, диалог с подсказкой
- Дочерние сессии переносятся вместе с родителем (каскадный UPDATE WHERE parent_id=?)

## Hierarchical tree
- Treeview `show="tree headings"` с column #0
- `_refresh_tree()` строит parent→children map, вставляет детей под родителя
- Чекбокс «Sub» — скрыть/показать subagent
- Subagent-строки: тег `subagent` (серый курсив, обе темы)
- Колонка «Дочер.» — число детей для корневых, `→` для subagent

## Documentation Standards (см. ~/.config/opencode/CONTEXT_RULES.md)
- Doc-ID система для всех .md файлов
- Detailed Response Format (7 секций: Контекст → Исследование → Выводы → Реализация → Проверка → Риски → Ссылки)
- Architecture Documentation: новый функционал → feature doc (≥100 строк) + reference/ + CHANGELOG + справка
- Documentation Quality Gate перед завершением таска
