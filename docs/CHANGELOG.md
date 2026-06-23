# Changelog — OpenCode Session Manager + Desktop Dev

> Doc-ID: CHANGELOG | Последнее обновление: 2026-06-23

## 2026-06-23 — v2.0: Electron Dev Mode Optimization + Bug Fixes

### КРИТИЧЕСКИЕ ИСПРАВЛЕНИЯ

#### [BUG-EPIPE-1] EPIPE: broken pipe crash
**Симптом:** "A JavaScript error occurred in the main process" — dialog при каждом запуске.
**Причина:** `electron-log` пытается писать в stdout/stderr после закрытия pipe parent процессом. Синхронный try-catch не ловит асинхронный EPIPE.
**Решение:** `logging.ts` — добавлены `process.stdout.on("error")` и `process.stderr.on("error")` handlers.
**Файл:** `packages/desktop/src/main/logging.ts:202-214`

#### [BUG-CATCHALL-1] Effect.catchAll is not a function
**Симптом:** `TypeError: exports_Effect.catchAll is not a function` в bundled output.
**Причина:** Бандлер Vite/Rollup переименовывал `Effect.catch` → `Effect.catchAll` при tree-shaking. `catchAll` не существует в Effect v4 (beta.74).
**Решение:** Убран кастомный catch-обработчик из `database.ts`. Исправлен pre-built dist: `exports_Effect.catchAll` → `exports_Effect.catch` в `dist/node/node.js`.
**Файлы:** `packages/core/src/database/database.ts`, `packages/opencode/dist/node/node.js`

#### [BUG-MALFORMED-1] database disk image is malformed
**Симптом:** opencode не запускается, БД не открывается.
**Причина:** При замене .db файла остаются -wal и -shm от старой схемы. SQLite replay WAL от v47 поверх v39 → краш.
**Решение:**
1. `database.ts` — маркер `.needs-wal-repair` для авто-ремонта WAL/SHM
2. `sqlite.bun.ts` / `sqlite.node.ts` — HINT-сообщения при malformed с инструкциями по восстановлению
3. `opencode-manager/core.py` — `check_db_health()` для диагностики
4. MCP: `oc_health_check`, `oc_repair_wal`
**Документация:** `docs/guides/database-malformed.md` (Doc-ID: DB-MALFORMED-1)

#### [BUG-HTML-1] malformed HTML: button-in-button
**Симптом:** Vite warning "The HTML provided is malformed" при каждом запуске.
**Причина:** `ServerDirectory.tsx` оборачивал `<Button>` и `<button>Copy Link` в `<button>`. Браузер ломает вложенную структуру.
**Решение:** Внешний `<button>` → `<div role="button" tabindex="0">` с `onKeyDown` для клавиатурной доступности.
**Файл:** `packages/tool-panel/src/components/mcp/ServerDirectory.tsx:39-71`

#### [BUG-DB-LOCK-1] backgroundIntegrityCheck блокировал БД
**Симптом:** Sidecar зависал на 60s timeout при каждом запуске.
**Причина:** `backgroundIntegrityCheck()` открывал второе соединение к БД через `node:sqlite` одновременно с основным через `bun:sqlite`. WAL конфликт → deadlock.
**Решение:** `backgroundIntegrityCheck` удалён. Авто-ремонт WAL/SHM теперь через файловый маркер (не SQL).
**Файл:** `packages/core/src/database/database.ts`

### УСКОРЕНИЕ ELECTRON DEV MODE

#### [PERF-DEV-1] Vite SSR build: 4 min → 4s
**Проблема:** electron-vite всегда запускал полную Rollup сборку 20MB серверного dist даже в dev.
**Решение:**
1. **Externalize** — `virtual:opencode-server` не бандлится Rollup в dev mode
2. **Bun sidecar** — dev mode спавнит `bun run packages/opencode/src/index.ts serve` напрямую (TypeScript, без сборки)
3. **Shell: true** — `spawn("bun", ...)` с `shell: true` для Windows совместимости
4. **Удалён auto-rebuild** — не ждёт 30s при каждом старте

**Изменённые файлы:**
- `packages/desktop/electron.vite.config.ts` — `external: isDev ? [...] : []`, `resolve-server-module` plugin
- `packages/desktop/src/main/server.ts` — dev branch с `spawn("bun", ...)`
- `packages/desktop/src/main/sidecar-bun.ts` — новый dev-mode sidecar (не используется, main process спавнит напрямую)

**Результат:**

| Этап | До | После |
|------|-----|-------|
| Vite SSR build (main) | 3-4 min | **4.10s** |
| Dist rebuild | 30s (auto) | 0s (Bun runs TS) |
| Server startup | внутри Rollup | **~30s** (Bun import) |
| **Полный restart** | **4-5 min** | **< 1 min** |

### ИСПРАВЛЕНИЯ ДЛЯ opencode-manager

#### [MCP-DB-002] Новые MCP инструменты
- `oc_health_check` — глубокая проверка ВСЕХ .db файлов с suggestions
- `oc_repair_wal` — безопасное удаление WAL/SHM с backup и verify
- `oc_list_dbs` — обновлён: WAL/SHM статус, openable/error

#### [CORE-002] OpenCodeDB.check_db_health()
- Проверка `PRAGMA integrity_check`
- Обнаружение WAL/SHM конфликтов
- Проверка schema_version и отсутствующих таблиц
- Подсчёт orphaned session_diff файлов
- Проверка lost_and_found таблицы

### ДОКУМЕНТАЦИЯ

| Документ | Doc-ID | Описание |
|----------|--------|----------|
| `docs/guides/database-malformed.md` | DB-MALFORMED-1 | Полная диагностика и восстановление malformed БД |
| `packages/desktop/docs/stale-dist-and-catchall.md` | DESKTOP-STALE-DIST-1 | Проблема stale dist, Effect.catch, dev workflow |
| `packages/desktop/docs/electron-dev-optimization.md` | — | Архитектура и настройка dev mode |

### БЭКАПЫ

| Дата | Расположение | Содержимое |
|------|-------------|------------|
| 2026-06-22 | `C:\Users\misch\.local\share\opencode\backups\pre_restore_20260622_192859\` | Все .db файлы до修复 |
| 2026-06-23 | `Q:\OPENCODEBACKUP\refactor-20260623_093137\` | Все изменяемые файлы до Phase A+B |

---

## 2026-06-22 — v1.8

### Добавлено
- **[DB-MALFORMED-1]** Диагностика и восстановление "database disk image is malformed":
  - `OpenCodeDB.check_db_health(path)` — полная проверка здоровья БД:
    - Определение malformed баз (PRAGMA integrity_check)
    - Обнаружение WAL/SHH конфликтов (stale WAL от другой схемы)
    - Проверка schema_version и отсутствующих таблиц
    - Подсчёт orphaned session_diff файлов
    - Проверка lost_and_found таблицы
  - `OpenCodeDB.repair_wal_conflict(path, dry_run)` — удаление stale WAL/SHM
  - Классы `DBHealthIssue` и `DBHealthReport` для структурированной диагностики
- **[MCP-DB-MALFORMED-1]** Новые MCP-инструменты:
  - `oc_health_check` — глубокая проверка ВСЕХ .db файлов с suggestions по исправлению
  - `oc_repair_wal` — безопасное удаление WAL/SHM с backup и verify
  - Обновлён `oc_list_dbs` — теперь показывает WAL/SHM статус и openable/error для каждой базы
- **[DOC-MALFORMED-1]** Документация `docs/guides/database-malformed.md`:
  - Объяснение корневой причины (WAL/SHM конфликт)
  - 5 методов восстановления
  - Структура файлов в ~/.local/share/opencode
  - Профилактика и известные проблемы

### Улучшено
- `core.py` — `list_databases()` теперь возвращает WAL/SHM статус и openable/error

## 2026-06-21 — v1.7

### Добавлено
- **[FIX-ENCODING-1]** Новый инструмент MCP для восстановления кодировки:
  - `oc_detect_mojibake` — сканирование session.title на CP866 mojibake
  - `oc_fix_mojibake` — исправление (с backup, confirm=true, dry_run)
  - Алгоритм: UTF-8 байты → прочитаны как CP866 → сохранены как Unicode.
    Обратное преобразование через CP866 lookup-таблицу.
- **[FIX-ENCODING-1]** Документация `docs/features/fix-encoding.md`:
  - Полная CP866→UTF-8 таблица
  - Примеры кода на Python и TypeScript
  - Диагностика и предотвращение

## 2026-06-22 — v1.6

### Добавлено
- **[MCP-DB-001]** Новая диагностика в MCP `opencode-db`:
  - `db_health` — быстрая проверка всех известных проблем одной командой
  - `db_find_broken` — расширенное сканирование: пустые sandboxes, битая кодировка, пустой JSON
  - `db_fix` — новый флаг `fix_encoding=true` для исправления mojibake
  - `db_repair_encoding` — автономное исправление битой кириллицы во всей БД
- **[GUIDE-TROUBLE-1]** Новый раздел в troubleshooting:
  - 8.1 Пустые сессии → "Не удалось загрузить сессии"
  - 8.2 Пустые sandboxes → 500 при загрузке (JSON.parse ошибка)
  - 8.3 Пустые message.data → 500 при fetchMessages
  - 8.4 Битая кириллица (mojibake) — алгоритм фикса
  - 8.5 Осиротевшие события
  - 9. Профилактика и автоматические проверки
- **[GUIDE-CORRUPT-1]** Новый раздел E.4: пост-восстановление, проверка `db_health()`

### Изменено
- **`mcp-servers/opencode-db/index.ts`** — полный рефакторинг:
  - Авто-детекция mojibake с верификацией (реальные кириллические символы)
  - Исправление битой кодировки: `Buffer.from(text, "latin1").toString("utf-8")`
  - Глубокое сканирование всех JSON-полей во всех таблицах
  - Починка осиротевших event_sequence

## 2026-06-21 — v1.5

### Добавлено
- **[GUIDE-CORRUPT-2]** Инцидент 21 июня: полная потеря sqlite_master
  - Новый тип повреждения: sqlite_master/header, а не B-tree leaf pages
  - `.recover` восстановил **148/148 сессий** (100%, vs 139/279 в инциденте 1)
  - Причина `.recover` работает на уровне pages, а не B-tree
  - Добавлены ловушки: PowerShell `<` redirection, UTF-16 конвертация, OOM
  - Быстрый чеклист восстановления (приложение E)

## 2026-06-20 — v1.4

### Добавлено
- **[GUIDE-CORRUPT-1]** Руководство по восстановлению повреждённой БД (`docs/guides/corrupted-db-recovery.md`)
  - Полная документация инцидента SQLITE_CORRUPT (8-19 июня 2026)
  - Описание структуры БД opencode (таблицы, связи, event types)
  - 6 методов восстановления с результатами
  - Raw binary extraction: алгоритм, проблемы, решения
  - MCP Fast Search: memchr+memcmp алгоритм (как в Notepad++)
  - Lost_and_found extraction: извлечение 350 записей для 79 сессий
  - Импорт 5 сессий с JSON данными в opencode2_final.db
  - Known limitations, профилактика
  - **Итог:** 140 сессий, 11,916 сообщений, 45,954 части (было 139/11904/45920)

### Изменено
- **troubleshooting.md** — добавлена ссылка на [GUIDE-CORRUPT-1] в раздел 2.3

## 2026-06-02 — v1.3

### Добавлено
- **`docs_server.py`** — локальный HTTP-сервер документации (порт 8765)
  - Конвертирует .md → HTML на лету (заголовки, код, таблицы, списки)
  - Авто-открытие браузера при запуске
  - Тёмная/светлая тема через `prefers-color-scheme`
  - Landing page `docs/index.html` с карточками-ссылками на все разделы
- **Кнопка «Открыть документацию»** на вкладке «Справка»
  - Запускает `docs_server.py` через `subprocess.Popen`
  - Показывает статус: «Сервер запущен на http://127.0.0.1:8765»
  - Стиль `Accent.TButton` (синяя кнопка)

### Изменено
- **Вкладка «Справка» переписана:** вместо 1482 строк встроенного текста — минимальная страница-приветствие + кнопка запуска docs-сервера
- Удалён мёртвый код (баг с `state=tk.DISABLED`) и дубликаты секций
- Удалён `_build_help_content` (был 1772 строки, заменён на 60 строк)

### Удалено
- Все разделы справки (2-17) перенесены в отдельные .md файлы в `docs/`
- MCP-описания, SQL-запросы, схемы БД, troubleshooting, глоссарий — теперь доступны через веб-сервер

## 2026-06-02 — v1.2

### Добавлено
- **[ARCHIVE-1]** Архивация сессий
  - Колонка «Сост.» — `🗄` для архивных, пусто для активных
  - Кнопки «Архивировать» / «Разархивировать» в панели действий
  - Авто-переключение активности кнопок по статусу выделенных сессий
  - Защита от запущенного OpenCode (`_check_opencode()`)
  - Стилизация архивных строк: серый текст (`#8b949e` тёмная / `#9ca3af` светлая)
- **[MCP-OCDB-1]** MCP-сервер `mcp-opencode-db.py` для инспекции SQLite-баз opencode
  - 6 инструментов: список БД, сессии, полная сессия, дети, орфаны, SQL-запрос
  - Подключён в глобальный `opencode.jsonc`
- **[H-TREE-1]** Иерархическое дерево сессий (родитель → дочерние subagent)
  - Treeview column #0 с отступами, колонка «Дочер.»
  - Subagent-строки серым курсивом (обе темы)
  - Чекбокс «Sub» для скрытия/показа subagent
  - Кнопка «Перенести в проект» отключена для subagent
  - Поиск включает родителя если найден ребёнок
- **[DB-SELECTOR-1]** Выбор БД из выпадающего списка
  - Автоопределение БД с наибольшим числом сессий
  - OpenCodeDB.list_databases() — сканирование всех opencode*.db
  - Переключение между БД в реальном времени
- **Orphan-отображение:** 5 orphan-детей (родитель удалён) показываются красным (`⚠`) в отдельной секции дерева
- **BUGS.md:** файл `docs/bugs/README.md` с трекингом багов по Doc-ID

### Исправлено
- **[S-MOVE-2]** `update_session_directory` теперь обновляет `project_id`, `path`, и рекурсивно дочерние сессии
- **[S-MOVE-2]** `_resolve_project_id` — определяет project_id по git-корню директории
- **[DB-SELECTOR-1]** `_detect_db_path` переписан: сканирует все БД, выбирает с макс. сессий
- **Desktop-совместимость:** сессия добавлена в `globalSync.project` и `sessionTabs` Desktop-приложения
- **BOM в `global.dat`:** Исправлена запись JSON без BOM (Tauri Store не понимает BOM)
- **BUG-003 (aws):** 2 заархивированные aws-сессии расархивированы, 14 корневых теперь видны
- **BUG-003 (aws):** `globalSync.project` обновлён — 13 aws-сессий добавлены в индекс глобального проекта
- **[ARCHIVE-1]** `import time` в `core.py` — `archive_session()` падал с `name 'time' is not defined`

### Документация
- `docs/features/` — созданы **5 файлов** с Doc-ID для каждого функционала
- `docs/bugs/README.md` — трекинг багов и ограничений
- `CONTEXT_RULES.md` (глобальный + проектный) — добавлены секции:
  - **Documentation Standards** — Doc-ID, Cross-Reference
  - **Detailed Response Format** — формат описания изменений
  - **Architecture Documentation** — обязательное документирование нового функционала
- Справка в GUI (вкладка Справка) — добавлены разделы 2.10–2.14:
  - Иерархическое дерево (2.10)
  - Выбор БД (2.11)
  - MCP-инспектор (2.12)
  - Известные ограничения (2.13, перенумерованы)
  - Архивация сессий (2.14)

### Известные проблемы
- `opencode-dev.db` не содержит TestQA-сессии (32 шт.) — они только в `opencode.db`
- 5 orphan-детей в `opencode.db` (родители удалены)
- `opencode session list` не работает в dev-сборке `0.0.0-dev-202606012329`
