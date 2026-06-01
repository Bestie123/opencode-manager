# Архитектура OpenCode и устранение неполадок

> **Для встраивания в сам opencode:** см. `ARCHITECTURE-PR.md` — без привязки к этому проекту, готово к PR.

> Создано: 2026-06-01
> Контекст: После инцидента с пропажей сессий в OpenCode Desktop

## Содержание

1. [Два OpenCode](#1-два-opencode)
2. [Базы данных](#2-базы-данных)
3. [Схема данных SQLite](#3-схема-данных-sqlite)
4. [Жизненный цикл сессии](#4-жизненный-цикл-сессии)
5. [Desktop-приложение (Tauri)](#5-desktop-приложение-tauri)
6. [Session Manager (opencode-manager)](#6-session-manager-opencode-manager)
7. [Кнопка "Перенести в проект"](#7-кнопка-перенести-в-проект)
8. [История инцидента](#8-история-инцидента)
9. [Чеклист диагностики](#9-чеклист-диагностики)
10. [Термины и определения](#10-термины-и-определения)

---

## 1. Два OpenCode

На системе пользователя установлены ДВЕ копии OpenCode, которые используют разные базы данных:

### 1.1 OpenCode CLI / TUI (dev)

| Свойство | Значение |
|----------|----------|
| Путь | `C:\Users\misch\AppData\Roaming\npm\node_modules\opencode` |
| Канал | `dev` |
| Версия | Сборка из репозитория `Новая папка (4)\opencode` |
| База данных | `opencode-dev.db` |
| Сессий | ~5 |
| Запуск | Из терминала: `opencode` |

### 1.2 OpenCode Desktop

| Свойство | Значение |
|----------|----------|
| Путь | `C:\Users\misch\AppData\Local\Programs\@opencode-aidesktop\OpenCode.exe` |
| Канал | `latest` |
| Версия | `1.15.13` (packaged, Tauri) |
| База данных | `opencode.db` |
| Сессий | ~56 |
| Запуск | Через ярлык / меню Пуск |

### 1.3 Как OpenCode выбирает базу данных

Файл: `packages/core/src/database/database.ts`

```typescript
function path() {
  // 1. OPENCODE_DB env var — абсолютный override
  if (Flag.OPENCODE_DB) { return isAbsolute ? Flag.OPENCODE_DB : join(data, Flag.OPENCODE_DB) }
  
  // 2. Каналы latest/beta/prod → opencode.db
  if (["latest", "beta", "prod"].includes(InstallationChannel) ||
      process.env.OPENCODE_DISABLE_CHANNEL_DB)
    return join(Global.Path.data, "opencode.db")
  
  // 3. Всё остальное → opencode-{channel}.db
  return join(Global.Path.data, `opencode-${InstallationChannel}.db`)
}
```

**Важно:** Desktop и CLI могут использовать РАЗНЫЕ базы данных. Всегда проверяй `opencode db path`.

### 1.4 Где хранятся базы

```
C:\Users\misch\.local\share\opencode\
├── opencode.db                  # 56 сессий (Desktop, latest)
├── opencode-dev.db              # 5 сессий (CLI, dev)
├── opencode1.db                 # 51 сессия (копия)
├── opencode - Копия.db          # 51 сессия (копия)
├── opencode - Копия (2).db      # 56 сессий (копия)
├── storage\session_diff\        # Diff-файлы сессий
└── snapshot\                    # Снапшоты git-объектов
```

---

## 2. Схема данных SQLite

### 2.1 Таблица `project`

```sql
CREATE TABLE project (
    id TEXT PRIMARY KEY,          -- 'global' для корневого, или хеш для конкретного
    worktree TEXT,                -- '/' для global, или путь к git-root
    vcs TEXT,                     -- 'git' или NULL
    name TEXT,
    icon_url TEXT,
    icon_color TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    time_initialized INTEGER,
    sandboxes TEXT,               -- JSON array дополнительных директорий
    commands TEXT,
    icon_url_override TEXT
);
```

Примеры записей:

```
id=global  worktree='/'        vcs=NULL   — глобальный проект (вне git-реп)
id=хеш_TestQA  worktree='Q:\...\TestQA'   vcs=git   — проект TestQA
```

**Критическое правило:** `global` проект ДОЛЖЕН иметь `worktree='/'` и `vcs=NULL`. Если эти поля сбиты, opencode не может определить контекст сессии.

### 2.2 Таблица `session`

```sql
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,              -- ссылка на project.id (или 'global')
    parent_id TEXT,               -- для subagent-сессий
    slug TEXT,
    directory TEXT,               -- путь на диске (backslashes на Windows)
    path TEXT,                    -- путь с forward slashes
    title TEXT,
    version TEXT,
    model TEXT,                   -- JSON или строка
    time_created INTEGER,         -- unix timestamp в ms
    time_updated INTEGER,
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_reasoning INTEGER,
    tokens_cache_read INTEGER,
    tokens_cache_write INTEGER,
    cost REAL,
    ...
);
```

**Правила для полей пути:**

| Поле | Формат | Пример |
|------|--------|--------|
| `directory` | `\` (backslashes) | `Q:\User_Data\Desktop\TestQA` |
| `path` | `/` (forward slashes) | `Q:/User_Data/Desktop/TestQA` |

**Нарушение этих форматов → сессия не отображается!**

### 2.3 Таблицы `message` и `part`

```sql
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                    -- JSON: { role, summary, ... }
);

CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                    -- JSON: { type, text, state, tool, ... }
);
```

`data` в `message` содержит role (`user`, `assistant`).
`data` в `part` содержит type (`text`, `tool`, `reasoning`, `patch`, `step-start`, `step-finish`, `compaction`, `file`).

---

## 3. Как OpenCode определяет текущий проект

### 3.1 `ProjectV2.resolve(directory)`

Файл: `packages/core/src/project.ts`

```typescript
resolve(input: AbsolutePath) {
  const repo = git.find(input)        // Поиск git-репозитория
  if (!repo) return { id: 'global', directory: корень_диска, vcs: undefined }
  
  const previous = cached(repo.store) // Чтение .git/opencode (хеш проекта из прошлого раза)
  const id = remote(repo) ?? previous ?? root(repo) ?? 'global'
  //   1. Хеш git-remote URL
  //   2. Хеш из .git/opencode (кэш)
  //   3. Хеш пути git-root
  //   4. 'global' если ничего не сработало
  
  return { previous, id, directory: repo.directory, vcs: { type: 'git' } }
}
```

### 3.2 `fromDirectory(directory)`

Файл: `packages/opencode/src/project/project.ts`

Связывает разрешённый проект с БД:

```typescript
function* fromDirectory(directory) {
  const data = yield* ProjectV2.resolve(directory)
  
  // Для глобального проекта worktree=/, иначе = директория
  const worktree = data.id === 'global' && !data.vcs ? "/" : data.directory
  
  // Миграция project_id (если у проекта сменился ID)
  yield* migrateProjectId(data.previous, data.id)
  
  // UPSERT проекта в таблицу project
  yield* db.insert(ProjectTable).values({ id: projectID, worktree, ... })
    .onConflictDoUpdate(...)
  
  // Миграция сессий: перенос сессий из 'global' в projectID
  if (projectID !== 'global') {
    yield* db.update(SessionTable)
      .set({ project_id: projectID })
      .where(and(
        eq(SessionTable.project_id, 'global'),
        eq(SessionTable.directory, data.directory)
      ))
  }
}
```

### 3.3 Фильтрация сессий в TUI

Файл: `packages/opencode/src/session/session.ts`

```typescript
function listByProject(db, input) {
  const conditions = [eq(SessionTable.project_id, input.projectID)]
  // + parent_id IS NULL (roots only)
  // + time_updated > 30 days
  // + LIKE на path если передан
  return db.select().from(SessionTable).where(and(...conditions))
}
```

TUI всегда фильтрует по `project_id` текущего проекта. Сессии с другим `project_id` НЕ ПОКАЗЫВАЮТСЯ.

---

## 4. Desktop-приложение (Tauri, v1.15.13)

### 4.1 Архитектура

```
┌──────────────────┐     HTTP API      ┌──────────────────┐
│  OpenCode.exe    │ ◄──────────────►  │  OpenCode.exe    │
│  (UI / Tauri)    │  localhost:XXXXX  │  (sidecar server)│
└──────────────────┘                   └──────┬───────────┘
                                              │
                                              ▼
                                     ┌──────────────────┐
                                     │  opencode.db      │
                                     │  (SQLite, latest) │
                                     └──────────────────┘
```

- Desktop-приложение — Tauri (Rust + WebView)
- При запуске спавнит собственный бинарник как sidecar: `OpenCode.exe server --port XXXXX`
- Sidecar — это HTTP API сервер
- Desktop общается с sidecar через `localhost:XXXXX`

### 4.2 State-файлы (Tauri Store)

Расположение: `%APPDATA%\ai.opencode.desktop\`

| Файл | Назначение | Формат |
|------|-----------|--------|
| `opencode.global.dat` | Основное состояние | JSON (complex, с вложенными строками) |
| `opencode.workspace.{path}.{rand}.dat` | Per-workspace данные | JSON |
| `opencode.settings` | `{"tauriMigrated": true}` | JSON |
| `default.dat` | Кэш настроек UI | JSON |
| `opencode.locks\` | Блокировки sidecar | файлы |

**Критическое:** Tauri Store не поддерживает UTF-8 BOM (`EF BB BF`). JSON должен быть чистым UTF-8 без BOM. Если BOM есть, store падает с ошибкой парсинга.

### 4.3 Логи Desktop-приложения

```
%APPDATA%\ai.opencode.desktop\logs\{timestamp}\
├── main.log       — основной процесс
├── server.log     — sidecar process
├── renderer.log   — UI (WebView) — здесь ошибки рендеринга
├── crash.log      — crash reporter
├── network.log    — HTTP логи
└── utility.log    — утилиты
```

---

## 5. Session Manager (opencode-manager)

### 5.1 Структура

```
Q:\User_Data\Desktop\opencode-manager\
├── core.py     — работа с БД (OpenCodeDB, OpenCodeCLI)
├── app.py      — GUI на Tkinter
├── run.py      — точка входа
└── start.bat/ps1 — лаунчеры
```

### 5.2 Ключевые классы

**`OpenCodeDB`** — интерфейс к SQLite (чтение, запись, удаление сессий).

**`OpenCodeCLI`** — экспорт/импорт сессий в JSON.

### 5.3 `_detect_db_path()` — автоопределение БД

```python
@staticmethod
def _detect_db_path() -> str:
    # 1. Ищет все opencode*.db в ~/.local/share/opencode/
    # 2. Выбирает ту, где больше всего сессий
    # 3. При равенстве — предпочитает opencode.db
    # 4. Fallback: opencode db path (CLI)
```

**Известный баг (исправлен):** Глоб `opencode*.db` находит копии (`opencode - Копия (2).db`). При равном числе сессий сортировка выбирала копию по алфавиту. Исправлено приоритетом `opencode.db`.

### 5.4 `update_session_directory()` — "Перенести в проект"

```python
def update_session_directory(self, session_id, new_directory):
    new_project_id = self._resolve_project_id(new_directory)
    new_path = new_directory.replace("\\", "/")
    
    UPDATE session
    SET directory = ?, path = ?, project_id = ?
    WHERE id = ?                   -- основная сессия
    -- AND WHERE parent_id = ?     -- дочерние (subagent)
```

**Правильная работа:**
- `directory` = путь с backslashes (как вернул `filedialog.askdirectory()`)
- `path` = тот же путь с forward slashes
- `project_id` = ID проекта, найденного по git-root, или `'global'`

**Потенциальные проблемы:**
- Если `new_directory` уже содержит forward slashes → нужно нормализовать
- `_resolve_project_id()` возвращает `'global'` если проект не найден в таблице `project`
- После переноса сессия получает `project_id='global'` и исчезает из старого проекта

---

## 6. История инцидента

### День 1: Пользователь перенёс сессию

1. Пользователь нажал "Перенести в проект" в Session Manager
2. Сессия исчезла из старого проекта и не появилась в новом
3. Пользователь сообщил об этом в opencode-сессии

### День 1 (продолжение): Попытка исправить

4. AI проанализировал `opencode-manager` и обнаружил:
   - `_detect_db_path` не определял правильную БД
   - `update_session_directory` не обновлял `project_id`
5. AI "исправил" автоопределение БД
6. Написал скрипт миграции сессии между БД
7. Миграция скопировала сессию в `opencode-dev.db`
8. **Результат:** Session Manager теперь показывал `opencode-dev.db` (5 сессий) вместо `opencode.db` (56)
9. AI начал править `opencode.global.dat` Desktop-приложения **ПРЯМЫМ РЕДАКТИРОВАНИЕМ** (Python write)
10. Desktop-приложение БЫЛО ЗАПУЩЕНО — изменения перезаписывались при выходе

### День 2: Восстановление

11. Откат: исправление `_detect_db_path` (приоритет `opencode.db`)
12. Анализ JSON-экспорта предыдущей сессии → понимание полной картины
13. Обнаружение повреждённого global проекта в `opencode-dev.db`
14. Обнаружение повреждённой сессии (forward slashes в directory)
15. Обнаружение, что Desktop и CLI используют РАЗНЫЕ БД
16. Перезапись повреждённого `opencode.global.dat` (BOM → без BOM, валидация JSON)

### Коренные причины

1. **Не различали две БД** — Desktop (`opencode.db`) и CLI (`opencode-dev.db`)
2. **Автоопределение БД** — выбор копии вместо оригинала
3. **Прямое редактирование Tauri Store** — нарушение формата
4. **Редактирование запущенного приложения** — изменения сбрасывались

---

## 7. Чеклист диагностики

Когда сессии не отображаются:

### Шаг 1: Какая БД?

```powershell
opencode db path
```

Если показывает `opencode-dev.db` — это dev-канал.
Если `opencode.db` — stable/latest.

Проверить канал:
```powershell
# В логах Desktop: channel: 'latest'
# В исходниках: OPENCODE_CHANNEL || 'local'
```

### Шаг 2: Есть ли сессии?

```powershell
opencode session list
# или прямой SQL:
sqlite3 opencode.db "SELECT COUNT(*) FROM session"
```

### Шаг 3: Проверить global проект

```sql
SELECT id, worktree, vcs FROM project WHERE id = 'global';
```

Ожидается: `global | / | NULL`
Если не так:
```sql
UPDATE project SET worktree = '/', vcs = NULL WHERE id = 'global';
```

### Шаг 4: Проверить проекты в сессиях

```sql
SELECT project_id, COUNT(*) FROM session GROUP BY project_id;
```

Если есть `project_id = 'global'` — сессии глобального проекта.

### Шаг 5: Проверить форматы путей

```sql
SELECT id, directory, path FROM session 
WHERE directory LIKE '%/%' OR directory LIKE '%//%';
```

`directory` должен использовать `\`, `path` должен использовать `/`.

### Шаг 6: Логи Desktop-приложения

```powershell
Get-Content "$env:APPDATA\ai.opencode.desktop\logs\*\renderer.log"
```

Искать `SyntaxError`, `Unexpected token`, `store-set`.

### Шаг 7: State-файлы Desktop

```powershell
# Проверить валидность JSON
py -c "import json; json.load(open(r'$env:APPDATA\ai.opencode.desktop\opencode.global.dat'))"
```

Ошибка парсинга → исправить или удалить файл (пересоздастся).

---

## 8. Термины

| Термин | Определение |
|--------|-----------|
| Сессия | Один диалог с opencode (user + assistant сообщения) |
| Проект | Git-репозиторий с которым работает opencode |
| Global project | Виртуальный проект для сессий вне git-репозиториев |
| Project ID | UUID проекта в таблице `project` (или 'global') |
| Subagent | Вложенная сессия (@explore, @general) с `parent_id` |
| Sidecar | Фоновый процесс `opencode server`, HTTP API бэкенд |
| Channel | `dev`, `latest`, `beta`, `prod` — определяет имя БД |
| Tauri Store | Система хранения состояния Tauri-приложения (JSON файлы) |
| worktree | Путь к корню git-репозитория |
| BOM | Byte Order Mark (`EF BB BF`) — UTF-8 сигнатура, ломает Tauri Store |

---

## 9. Полезные запросы

```sql
-- Все сессии с деталями
SELECT s.id, s.title, s.project_id, s.directory, s.path,
       s.time_created, s.time_updated
FROM session s
ORDER BY s.time_updated DESC;

-- Сессии по проектам
SELECT p.id, p.worktree, COUNT(s.id) as session_count
FROM project p
LEFT JOIN session s ON s.project_id = p.id
GROUP BY p.id;

-- Сколько сессий на проектах
SELECT project_id, COUNT(*) as cnt
FROM session
GROUP BY project_id;

-- Поиск коррумпированных путей
SELECT id, directory, path
FROM session
WHERE directory LIKE '%/%'
   OR path IS NULL
   OR path = '';

-- Сессии за последние 30 дней (roots only)
SELECT COUNT(*) FROM session
WHERE parent_id IS NULL
  AND time_updated > (strftime('%s','now') - 30*86400) * 1000;
```
