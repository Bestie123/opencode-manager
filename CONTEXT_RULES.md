# opencode-manager — Context Rules

## Project
GUI-инструмент для управления сессиями OpenCode через SQLite-базу.

## Stack
- Python 3.10+ (только stdlib: tkinter, sqlite3, json, threading)
- Без внешних зависимостей

## Code structure
- `app.py` — GUI на tkinter (класс App, 5 вкладок + меню + статус-бар)
- `core.py` — работа с БД (OpenCodeDB: чтение/запись opencode.db, OpenCodeCLI: экспорт/импорт)
- `run.py` — точка входа
- `start.bat` / `start.ps1` — лаунчеры

## Key classes
- `App(tk.Tk)` — главное окно, все вкладки и методы
- `OpenCodeDB` — все запросы к opencode.db
- `OpenCodeCLI` — вызов opencode CLI для экспорта/импорта
- `SessionInfo` — namedtuple с полями сессии
- `DBStats` — namedtuple со статистикой

## Theme
- `_toggle_theme()` — переключение тёмной/светлой темы
- Цвета задаются через `style.configure()` (ttk) и `.configure()` (tk-виджеты)
- **Обе ветки (dark/light) должны явно задавать ОДИНАКОВЫЙ набор свойств** — иначе при переключении остаются значения от предыдущей темы
- `style.theme_use("clam")` — только в `_setup_styles()`, не при каждом переключении
- Сохранение: `~/.opencode-manager/config.json` (`dark_mode: bool`)

## DB (readonly)
- `C:\Users\misch\.local\share\opencode\opencode.db` — SQLite
- Таблицы: session, message, part, todo, session_share
- message: `data` (JSON) содержит `role`, `summary.diffs` и т.д.
- part: `data` (JSON) содержит `type` (text/tool/reasoning/patch/step-start/step-finish/compaction/file)

## Key methods in app.py
- `_load_sessions()` — загрузка списка сессий в потоке
- `_refresh_messages()` — загрузка сообщений сессии с lazy-loading
- `_show_chat_message(msg)` — отображение диалога в чат-формате
- `_delete_selected_messages()` — удаление сообщений (удаляет и из part, и из message)
- `_update_session_counters()` — пересчёт токенов в сессии (ВСЕГДА коммитит)
- `_toggle_theme()` — переключение темы

## Filters
- `get_chat_messages(has_parts_only=True)` — скрыть компактированные сообщения
- `get_chat_messages_count(has_parts_only=True)` — количество с фильтром

## Lazy loading (Messages tab)
- По 500 записей, подгрузка при прокрутке >85%
- Сортировка: дата — перезапрос из БД, тип/статус/размер — in-memory

## Known issues
- Нет функции бэкапа всей БД из интерфейса

## Move session to project
- `core.py`: `update_session_directory(session_id, new_directory)` — UPDATE поля `directory` в таблице `session`
- `app.py`: `_change_session_directory()` — кнопка «Перенести в проект» на вкладке «Сессии»
- Diff-файлы не перемещаются, только поле directory в БД
- Пути внутри diff'ов не меняются — они относительны корня проекта
