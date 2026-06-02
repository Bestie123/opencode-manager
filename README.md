# OpenCode Session Manager

> Doc-ID: README | Дата: 2026-06-02

GUI-инструмент для управления SQLite-базами данных [OpenCode](https://opencode.ai) — AI-агента для написания кода.

## Возможности

- **Иерархическое дерево сессий** — корневые + дочерние subagent с цветовой маркировкой
- **Архивация** — скрыть сессии без удаления, разархивировать в любой момент
- **Чат-просмотр** — сообщения сессии с подсветкой типов контента (text, tool, reasoning, patch)
- **Выбор БД** — переключение между opencode.db, opencode-dev.db и копиями
- **Экспорт/импорт JSON** — резервное копирование отдельных сессий
- **Strip Reasoning** — удаление thinking-токенов (экономия ~77% места)
- **Перенос сессий** между проектами с каскадным обновлением subagent
- **Vacuum и очистка** — оптимизация SQLite, удаление осиротевших файлов
- **MCP-инспектор** — инструменты для диагностики изнутри opencode-сессии

## Требования

- Python 3.10+
- tkinter (встроен в Python)
- Опционально: `pip install mcp` (для MCP-сервера)

## Быстрый старт

```bash
cd opencode-manager
python run.py
```

## Структура проекта

```
opencode-manager/
├── app.py                 # GUI (tkinter, 5 вкладок, меню)
├── core.py                # Работа с БД (OpenCodeDB, OpenCodeCLI)
├── run.py                 # Точка входа
├── mcp-opencode-db.py     # MCP-сервер для диагностики
├── CONTEXT_RULES.md       # Правила для AI-агента
├── test_core.py           # Тесты
└── docs/
    ├── README.md          # Этот файл
    ├── ARCHITECTURE.md    # Архитектура OpenCode и менеджера
    ├── CHANGELOG.md       # История изменений
    ├── bugs/README.md     # Трекинг багов
    ├── incidents/         # Расследования инцидентов
    ├── features/          # Описание каждого функционала
    ├── reference/         # Справочники (SQL, data model)
    ├── guides/            # Гайды (troubleshooting, migration)
    └── rules/             # Стандарты документирования
```

## Документация

Вся документация использует систему **Doc-ID** для кросс-ссылок.
См. `docs/rules/documentation-standards.md` для подробностей.

## Установка MCP

MCP-сервер добавляется в `~/.config/opencode/opencode.jsonc`:

```json
"mcp-opencode-db": {
  "type": "local",
  "command": ["C:\\Users\\...\\python.exe", "Q:\\...\\mcp-opencode-db.py"],
  "enabled": true
}
```

На Windows обязательно указывать полный путь к python.exe.
