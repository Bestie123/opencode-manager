# Восстановление повреждённой БД: SQLITE_CORRUPT / database disk image is malformed

> Doc-ID: GUIDE-CORRUPT-1 | Дата: 2026-06-21 | Связанные: [GUIDE-TROUBLE-1], [ARCH-v2], [REF-SQL-1]

## Содержание

1. [Инцидент](#1-инцидент)
2. [Симптомы](#2-симптомы)
3. [Структура БД opencode](#3-структура-бд-opencode)
4. [Методы восстановления](#4-методы-восстановления)
5. [Raw binary extraction](#5-raw-binary-extraction)
6. [Промпты и команды](#6-промпты-и-команды)
7. [Результаты](#7-результаты)
8. [Известные ограничения](#8-известные-ограничения)
9. [Профилактика](#9-профилактика)

---

## 1. Инцидент

### Дата
8–19 июня 2026

### Причина
Параллельная запись в SQLite базу данных со стороны:
- Основного процесса opencode (CLI/TUI)
- Desktop приложения (Electron)
- Бекап-приложения (opencode-manager)
- MCP-серверов (mcp-opencode-db)

SQLite поддерживает **одну писателю** процесса. При параллельной записи WAL (Write-Ahead Log) может быть повреждён, что приводит к порче B-tree структуры.

### Масштаб
| Метрика | Значение |
|---------|----------|
| Размер БД | 2.5 GB |
| Всего сессий | 279 |
| Повреждённых | 140 (50%) |
| Потеряно безвозвратно | ~61 сессия |
| Частично восстановлено | ~79 сессий |
| Полностью восстановлено | 139 сессий |

---

## 2. Симптомы

### 2.1 Основная ошибка
```
Error: database disk image is malformed
```

### 2.2 Диагностика
```sql
PRAGMA integrity_check;
-- Результат: "database disk image is malformed"
```

### 2.3 Поведение sqlite3
```bash
sqlite3 opencode.db ".tables"
-- Error: database disk image is malformed

sqlite3 opencode.db ".recover"
-- Частично работает: извлекает 139 из 279 сессий
```

### 2.4 B-tree повреждение
```
Session table rootpage = 11, type = 0x02 (interior node)
Interior node (page 2824) содержит 96 cells с child pointers
ВСЕ 96 child pointers невалидны (> file_pages)
Session IDs читаются из индекса, но leaf-страницы с данными повреждены
```

---

## 3. Структура БД opencode

### 3.1 Ключевые таблицы

```sql
-- Основные таблицы для восстановления
session          -- метаданные сессий (id, title, directory, project_id, ...)
message          -- V1 legacy сообщения (id, session_id, data JSON)
part             -- V1 legacy части сообщений (id, message_id, session_id, data JSON)
event            -- event sourcing лог (id, aggregate_id, seq, type, data JSON)
session_message  -- V2 projected сообщения
session_input    -- V2 durable inbox

-- Вспомогательные
project          -- проекты (id, worktree, name, ...)
event_sequence   -- счётчики последовательностей
migration        -- Applied migrations
```

### 3.2 Связи
```
project → session (1:N, CASCADE DELETE)
session → message (1:N, CASCADE DELETE)
message → part (1:N, CASCADE DELETE)
session → session_message (1:N, CASCADE DELETE)
event_sequence → event (1:N, CASCADE DELETE)
```

### 3.3 Формат данных
- `session.data` — JSON: `{ title, directory, projectID, slug, model, ... }`
- `message.data` — JSON: `{ role, modelID, providerID, cost, tokens, ... }`
- `part.data` — JSON: `{ type, text, tool, callID, state, ... }`
- `event.data` — JSON: полный payload события (session.updated.1, message.updated.1, message.part.updated.1)

### 3.4 Event types (ключевые для восстановления)
```
session.updated.1           — метаданные сессии
message.updated.1           — сообщение (user/assistant)
message.part.updated.1      — часть сообщения (text, tool, reasoning, step-start, step-finish)
session.next.step.started.1 — начало шага
session.next.step.ended.1   — конец шага
```

---

## 4. Методы восстановления

### 4.1 sqlite3 .recover (ЧАСТИЧНО РАБОТАЕТ)
```bash
sqlite3 opencode.db ".recover" > recovered.sql
sqlite3 opencode_new.db < recovered.sql
```
**Результат:** 139 из 279 сессий с полными данными.
**Проблема:** B-tree повреждение не даёт восстановить оставшиеся 140 сессий.

### 4.1.1 Encoding: почему иероглифы и как читать правильно

При использовании `sqlite3 .recover` на Windows вы можете увидеть иероглифы вместо русского текста. Это **НЕ** означает потерю данных.

**Причина:**
- `sqlite3.exe` на Windows выводит текст через консоль, которая использует кодировку **UTF-16 LE**
- При перенаправлении вывода в файл (`> recovered.sql`) получается UTF-16 LE файл
- Notepad открывает такой файл как ANSI (Windows-1251) — отсюда иероглифы
- Внутри SQLite данные хранятся как **UTF-8 TEXT** — они физически целы

**Как проверить что данные читаются (Bun.js — гарантированно UTF-8):**

Bun.js использует SQLite C API напрямую, читая TEXT колонки как **native UTF-8**:

```typescript
import { Database } from "bun:sqlite";
const db = new Database("opencode.db", { readonly: true });
const sessions = db.query("SELECT id, title FROM session").all();
// title содержит правильные русские символы
```

**Как конвертировать UTF-16 SQL → читаемый UTF-8 (ПОТОКОВО):**

```powershell
# НЕ использовать [IO.File]::ReadAllText — OOM на 2GB+
$reader = [System.IO.StreamReader]::new("recovered.sql", [System.Text.Encoding]::Unicode)
$writer = [System.IO.StreamWriter]::new("recovered-utf8.sql", $false, [System.Text.Encoding]::UTF8)
$buf = New-Object char[] 65536
while (($count = $reader.Read($buf, 0, $buf.Length)) -gt 0) {
    $writer.Write($buf, 0, $count)
}
$reader.Close(); $writer.Close()
```

**Как открыть `.sql` с русским текстом в Notepad:**
1. File → Open → выбрать файл
2. В диалоге: Encoding → **UTF-8** (не ANSI)
3. Или открыть в **VS Code** / **Notepad++** — они автоопределяют UTF-8

**Чеклист (чтобы никогда не видеть иероглифы):**
1. После `.recover` → **обязательно** конвертировать UTF-16 → UTF-8
2. Импорт через `cmd.exe` (не PowerShell): `cmd.exe /c "sqlite3 new.db < recovered-utf8.sql"`
3. Просмотр → Notepad++ / VS Code, или Notepad с UTF-8 encoding
4. Альтернатива: читать через Bun.js `require("bun:sqlite")` — всегда UTF-8

### 4.2 .recover с lost_and_found
```bash
sqlite3 opencode.db ".recover"
# SQLite создаёт таблицу lost_and_found с фрагментами данных
```
**Результат:** 79 сессий имеют записи в lost_and_found (3-176 entries каждая).
**Проблема:** JSON в entries обрезан, неполные данные.

### 4.3 Raw byte scan (НЕ сработало для bulk)
```bash
# Поиск всех 140 ID в бинарном файле
# Bun/Node.js Buffer.indexOf на 2.7GB — слишком медленно
# PowerShell byte-by-byte — ещё медленнее
```
**Проблема:** `Buffer.indexOf` в Bun на Windows не оптимизирован для 2.7GB файлов.
**Результат:** Первый вызов `indexOf` работает (~0.1s), но цикл из 140×N вызовов зависает.

### 4.4 ripgrep (rg) (ЧАСТИЧНО РАБОТАЕТ)
```bash
rg --no-ignore -c "ses_1257f20f3ffeNCKSEPEZaPizKQ" opencode.db
# Результат: 5 совпадений (работает для одного ID)
```
**Проблема:** При поиске всех 140 ID последовательно — зависит на бинарнике.
**Обход:** Искать по одному ID, не все сразу.

### 4.5 Notepad++ (РАБОТАЕТ)
- Использует memory-mapped файлы
- Boyer-Moore-like алгоритм поиска
- 162,662 совпадений для "session" за секунды
- **Лучший инструмент для визуального исследования**

### 4.6 Memory-mapped I/O (.NET)
```csharp
// PowerShell/.NET approach
$mmf = [System.IO.MemoryMappedFiles.MemoryMappedFile]::CreateFromFile($fs, ...)
$acc = $mmf.CreateViewAccessor(0, 0, Read)
$acc.ReadArray(offset, buffer, 0, len)
```
**Проблема:** Byte-by-byte поиск в PowerShell слишком медленный.

---

## 5. Raw binary extraction

### 5.1 Формат данных в бинарнике

Данные хранятся в SQLite page format. JSON payloads читаются как UTF-8 строки среди бинарных данных (NUL bytes, page headers).

**Паттерн поиска:**
```
[evt_id][binary_header]session.updated.1{"sessionID":"ses_...","info":{...}}
[evt_id][binary_header]message.updated.1{"sessionID":"ses_...","info":{...}}
[evt_id][binary_header]message.part.updated.1{"sessionID":"ses_...","part":{...}}
```

### 5.2 Алгоритм извлечения

```
1. Загрузить файл в память (RAM disk = быстро, ~15s для 2.7GB)
2. Для каждого missing session ID:
   a. Найти первое вхождение (data.indexOf(id, 0, "ascii"))
   b. Прочитать контекст ±64KB вокруг вхождения
   c. Найти event markers (session.updated.1{, message.updated.1{, ...)
   d. Извлечь JSON через brace counting
   e. Отфильтровать по session ID
3. Сохранить результаты в JSON файлы
```

### 5.3 Проблемы

| Проблема | Причина | Решение |
|----------|---------|---------|
| `Buffer.indexOf` зависает | Bun не оптимизирован для 2.7GB | Использовать rg или .NET |
| Index pages vs data pages | ID в index ≠ данные сессии | Искать event markers в контексте |
| NUL bytes ломают текст | SQLite binary format | Использовать binary-safe поиск |
| JSON обрезан | Данные повреждены | Отмечать как "truncated" |
| rg зависит на бинарнике | Line-based search | Искать по одному ID |

---

## 6. Промпты и команды

### 6.1 Диагностика
```sql
-- Проверка целостности
PRAGMA integrity_check;

-- Размер БД
SELECT page_count * page_size as size_bytes FROM pragma_page_count(), pragma_page_size();

-- Количество сессий
SELECT COUNT(*) FROM session;

-- Список таблиц
.tables

-- Схема таблицы session
.schema session
```

### 6.2 Поиск в бинарнике (PowerShell)
```powershell
# Поиск ID в raw файле
$stream = [System.IO.File]::OpenRead("R:\opencode-data\opencode.db")
$stream.Seek($offset, [System.IO.SeekOrigin]::Begin)
$buf = New-Object byte[] (128 * 1024)
$stream.Read($buf, 0, $buf.Length)
$text = [System.Text.Encoding]::UTF8.GetString($buf)
# Поиск JSON паттернов
$text -split "`n" | Where-Object { $_ -match '"sessionID"' }
```

### 6.3 Поиск через rg
```bash
# Один ID
rg --no-ignore -c "ses_XXXX" R:\opencode-data\opencode.db

# Все session.updated.1 события
rg --no-ignore -c "session\.updated\.1\{" R:\opencode-data\opencode.db
```

### 6.4 Извлечение через sqlite3
```bash
# .recover
sqlite3 opencode.db ".recover" > recovered.sql

# Извлечение конкретной сессии
sqlite3 opencode.db "SELECT * FROM session WHERE id = 'ses_XXXX';"

# Потерянные данные из lost_and_found
sqlite3 opencode.db "SELECT * FROM lost_and_found WHERE arg1 LIKE '%ses_XXXX%';"
```

---

## 7. Результаты

### 7.1 Восстановлено (Инцидент 3 — opencode (2).db, 21 июня 2026)

Применён комбинированный метод: Bun.js rowid-scan (Pass 1) + sqlite3 .recover.
`.recover` выполнен через PowerShell `Start-Process -RedirectStandardOutput` (гарантирует UTF-8 на выходе).

| Источник | Сессий | Сообщений | Частей | Метод |
|----------|--------|-----------|--------|-------|
| Bun.js Pass 1 (rowid scan) | 122 | 11,759 | 45,699 | Итерация по rowid, перехват SQLITE_CORRUPT |
| sqlite3 .recover | 139 | 6,434 | 45,920 | Page-level recovery |
| **Merged** (без дубликатов) | **139** | **11,792** | **45,437** | Объединение + обогащение заголовков |

**Все 139 сессий имеют правильные заголовки (включая русский текст).**

### 7.2 Статус по сессиям (Инцидент 3)

| Категория | Количество | Описание |
|-----------|-----------|----------|
| Полностью (данные + метаданные) | 124 | Сообщения + parts + session title |
| Метаданные без сообщений | 15 | Только session title + project_id |
| **Потеряно безвозвратно** | **140** | Страницы физически перезаписаны |

### 7.3 Ключевые уроки

1. **Bun.js SQLite читает native UTF-8** — никаких иероглифов в отличие от `sqlite3 .recover`
2. **Rowid-сканирование работает** когда индекс повреждён — обходит B-tree и читает страницы напрямую
3. **sqlite3 .recover через PowerShell Start-Process** выводит UTF-8 (не UTF-16 LE)
4. **Комбинированный подход даёт максимум**: Bun достаёт сообщения с readable страниц, .recover достаёт session metadata с других страниц

### 7.4 Причина потерь
```
SQLite B-tree structure:
  Session table rootpage = 11, type = 0x02 (interior node)
  Interior node (page 2824): 96 cells с child pointers
  Все 96 child pointers невалидны (> file_pages)
  
Index pages (строки ~130xxx, ~11560xxx):
  Содержат session IDs как ключи для поиска
  НЕ содержат данных сессий (title, messages, parts)
  
Leaf pages (повреждены):
  Содержали actual data (session metadata, messages, parts)
  Перезаписаны при параллельной WAL checkpoint операции
  Восстановление невозможно - данные физически отсутствуют
```

---

## 8. Известные ограничения

### 8.1 Bun Buffer.indexOf на Windows
- `Buffer.indexOf` на 2.7GB буфере зависает
- Причина: не оптимизирован для очень больших буферов
- Решение: использовать rg или .NET memory-mapped I/O

### 8.2 ripgrep на бинарных файлах
- rg работает построчно, бинарные данные ломают поиск
- `--binary` флаг не решает проблему полностью
- Решение: искать по одному ID, не bulk

### 8.3 SQLite .recover
- Не может восстановить данные с повреждёнными B-tree leaf pages
- Lost_and_found содержит только фрагменты
- JSON в lost_and_found обрезан

### 8.4 Notepad++ поиск
- Работает быстро (memory-mapped + Boyer-Moore)
- Но не позволяет программно извлекать данные
- Используется для визуальной диагностики

---

## 9. Профилактика

### 9.1 WAL mode
```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;
```

### 9.2 Не допускать параллельную запись
- **Один процесс** пишет в БД одновременно
- Закрывать opencode CLI перед запуском Desktop
- Не использовать opencode-manager для записи пока opencode запущен

### 9.3 Регулярные бекапы
```bash
# Копия БД (без блокировки)
sqlite3 opencode.db ".backup 'opencode_backup.db'"

# Или через opencode-manager (кнопка Vacuum + Export)
```

### 9.4 Мониторинг размера
```sql
-- Проверять размер БД
SELECT page_count * page_size / 1048576 AS size_mb FROM pragma_page_count(), pragma_page_size();

-- Если > 500MB — делать vacuum/strip reasoning
```

### 9.5 Правильное чтение БД (без иероглифов)

При работе с повреждённой БД для чтения данных всегда используйте **один из**:

1. **Bun.js `require("bun:sqlite")`** — читает TEXT колонки как native UTF-8. Гарантированно без иероглифов.
2. **sqlite3 .recover** — после конвертации UTF-16 → UTF-8 (секция 4.1.1). Открывать в Notepad++ или VS Code.
3. **Не используйте** `sqlite3 .dump` в консоли Windows — вывод в UTF-16 LE, иероглифы неизбежны.

---

## Приложение A: Файлы восстановления

| Файл | Путь | Описание |
|------|------|----------|
| Оригинал | `R:\opencode-data\opencode.db` | 2.71 GB, повреждён |
| Восстановленная | `C:\Users\misch\.local\share\opencode\opencode2_final.db` | 1081 MB, 139 сессий |
| SQL dump | `C:\Users\misch\AppData\Local\Temp\opencode\recovered_utf8.sql` | 1988 MB |
| Missing IDs | `C:\Users\misch\AppData\Local\Temp\opencode\missing_ids.json` | 140 ID |
| Raw scan | `R:\opencode-data\recovery2\` | JSON извлечения |

## Приложение B: MCP Fast Search

### B.1 Регистрация
```json
// ~/.config/opencode/mcp.json
"fast-search": {
  "type": "local",
  "command": [
    "C:\\Users\\misch\\AppData\\Local\\Programs\\Python\\Python310\\python.exe",
    "R:\\opencode-data\\mcp-fast-search.py",
    "--stdio"
  ],
  "enabled": true
}
```

### B.2 Команды
```bash
# Статистика файла
python R:\opencode-data\mcp-fast-search.py stats

# Поиск паттерна
python R:\opencode-data\mcp-fast-search.py search '{"pattern":"session.updated.1{","maxResults":10}'

# Поиск по session ID
python R:\opencode-data\mcp-fast-search.py session '{"sessionId":"ses_XXXX"}'

# Bulk поиск (один проход файла)
python R:\opencode-data\mcp-fast-search.py bulkSearch '{"patterns":["ses_123","ses_456"],"maxPerPattern":5}'
```

### B.3 Алгоритм
- **memchr +memcmp** (как в Notepad++/Scintilla)
- Python `bytes.find()` = C-optimized SIMD memchr
- Chunked reading: 64MB chunks с overlap
- JSON extraction: brace counting с UTF-8 decode

## Приложение C: Скрипты

### B.1 Поиск всех 140 ID (Bun — работает для первого вызова)
```javascript
const data = Buffer.from(await Bun.file(RAW).arrayBuffer())
for (const id of missingIds) {
  const idx = data.indexOf(id, 0, "ascii")  // работает ~0.1s
  console.log(id, idx !== -1 ? "FOUND" : "NOT FOUND")
}
```

### B.2 Извлечение JSON из контекста
```javascript
function extractJSON(buf, start) {
  let bs = -1
  for (let i = start; i < Math.min(start + 300, buf.length); i++) {
    if (buf[i] === 0x7B) { bs = i; break }
  }
  if (bs === -1) return null
  let depth = 0, inStr = false, esc = false
  for (let i = bs; i < Math.min(bs + 128*1024, buf.length); i++) {
    const c = buf[i]
    if (esc) { esc = false; continue }
    if (c === 0x5C && inStr) { esc = true; continue }
    if (c === 0x22) { inStr = !inStr; continue }
    if (inStr) continue
    if (c === 0x7B) depth++
    else if (c === 0x7D) { depth--; if (depth === 0) return buf.utf8Slice(bs, i + 1) }
  }
  return null
}
```

---

## Приложение D: Инцидент 21 июня 2026 — Полная потеря sqlite_master

### D.1 Симптомы

| Метрика | Инцидент 1 (8-19 июня) | Инцидент 2 (21 июня) |
|---------|----------------------|---------------------|
| `sessions` в `list_dbs` | 140 (частично) | **-1** (не читается) |
| `.tables` | Работает частично | **Ошибка** |
| `.recover` | 139/279 сессий | **148/148 — ВСЕ** |
| `PRAGMA integrity_check` | malformed | malformed |
| Корень повреждения | B-tree leaf pages | **sqlite_master / header** |

### D.2 Диагностика

```bash
# БД не открывается ни одним инструментом:
sqlite3 opencode-err.db ".tables"
# Error: database disk image is malformed

# MCP показывает sessions: -1
mcp-opencode-db_oc_list_dbs()
# "sessions": -1

# Bun sqlite module:
new Database("opencode-err.db", { readonly: true })
# throws "database disk image is malformed"

# Даже sqlite_master не читается:
sqlite3 opencode-err.db "SELECT * FROM sqlite_master;"
# Error: database disk image is malformed
```

### D.3 Причина

Повреждение на уровне **заголовка файла** или **sqlite_master** таблицы.
В отличие от инцидента 1 (повреждены leaf pages с данными сессий),
здесь повреждена **служебная таблица sqlite_master**, которая описывает
структуру всех остальных таблиц.

### D.4 Восстановление — РАБОТАЕТ ПОЛНОСТЬЮ

```bash
# Шаг 1: Скачать sqlite3 CLI (если не в PATH)
# https://www.sqlite.org/download.html → sqlite-tools-win-x64

# Шаг 2: .recover извлекает данные на уровне страниц
sqlite3 opencode-err.db ".recover" > recovered.sql
# Файл: ~2.2GB (UTF-16 LE на Windows из-за console codepage)

# Шаг 3: Конвертация UTF-16 → UTF-8 (ПОТОКОВАЯ, не ReadAllText!)
$reader = [System.IO.StreamReader]::new("recovered.sql", [System.Text.Encoding]::Unicode)
$writer = [System.IO.StreamWriter]::new("recovered-utf8.sql", $false, [System.Text.Encoding]::UTF8)
$buf = New-Object char[] 65536
while (($count = $reader.Read($buf, 0, $buf.Length)) -gt 0) {
    $writer.Write($buf, 0, $count)
}
$reader.Close(); $writer.Close()

# Шаг 4: Импорт в чистую БД (через cmd.exe, НЕ PowerShell!)
# PowerShell не поддерживает < redirection
cmd.exe /c "sqlite3.exe fixed.db < recovered-utf8.sql"

# Шаг 5: Проверка
sqlite3 fixed.db "SELECT COUNT(*) FROM session;"
# 148
sqlite3 fixed.db "PRAGMA integrity_check;"
# ok
```

### D.5 Результат

| Метрика | Значение |
|---------|----------|
| Сессий | 148 |
| Сообщений | 12,300 |
| Parts | 46,686 |
| Integrity | `ok` |
| Размер | 1,271 MB |
| Потери | **0** |

### D.6 Ловушки

| Ловушка | Решение |
|---------|---------|
| `sessions: -1` в list_dbs | MCP не может прочитать — используй sqlite3 CLI |
| PowerShell `<` не работает | Использовать `cmd.exe /c "file < input"` |
| `ReadAllText` OOM на 2.2GB | Использовать `StreamReader` потоково |
| UTF-16 LE вывод sqlite3.exe | Конвертировать через `[System.Text.Encoding]::Unicode` |
| `Bun.spawn().stdin.write()` undefined | Использовать `proc.stdin` как `WritableStream` через `.getWriter()` |

### D.7 Ключевой инсайт

**`.recover` работает на уровне SQLite pages**, а не через B-tree.
Даже когда sqlite_master повреждён и `.tables` не работает,
`.recover` обходит повреждение и извлекает данные из raw pages.

Это делает `.recover` **универсальным инструментом** для обоих типов повреждения:
- Повреждённые leaf pages (инцидент 1) → частичное восстановление
- Повреждённый sqlite_master (инцидент 2) → **полное** восстановление

---

## Приложение E: Быстрый чеклист восстановления

### E.1 Если sqlite_master цел (БД частично читается)

```
1. Bun.js: просканировать message/part по rowid, перехватить SQLITE_CORRUPT
   - для каждого readable message → достать parts → сгруппировать по session_id
   - сохранить как JSON с BOM (\uFEFF) для правильной кодировки в Notepad
2. sqlite3 .recover через PowerShell (избежать UTF-16 LE):
   Start-Process sqlite3.exe -ArgumentList "db.db",".recover" `
     -RedirectStandardOutput recovered.sql -NoNewWindow -Wait
3. cmd.exe /c "sqlite3.exe new.db < recovered.sql"
4. Обогатить JSON-сессии заголовками из recovered.db
5. Проверка: sqlite3 new.db "SELECT COUNT(*) FROM session; PRAGMA integrity_check;"
6. Копия на безопасный диск
```

### E.2 Если sqlite_master повреждён (БД не открывается вовсе)

```
1. sqlite3 db.db ".recover" > recovered.sql    (работает на уровне страниц)
2. Конвертация UTF-16 → UTF-8 (StreamReader, НЕ ReadAllText) если нужно
3. cmd.exe /c "sqlite3.exe new.db < recovered-utf8.sql"
4. Проверка: sqlite3 new.db "SELECT COUNT(*) FROM session; PRAGMA integrity_check;"
5. Копия на безопасный диск
```

### E.4 Пост-восстановление: проверка и фикс битых данных

После любого восстановления (любой метод E.1-E.3) обязательно запустите:

```javascript
// Через MCP opencode-db:
db_health()              // быстрая проверка всех проблем
db_fix(dry_run=true)     // предпросмотр фикса
db_fix(fix_encoding=true) // полная починка + encoding (рекомендуется)
```

**Что проверяется:**
- Пустые сессии → удаление
- Пустой JSON в `message.data` / `part.data` → фикс
- Пустые `project.sandboxes` → установка `'[]'`
- Битая кириллица (mojibake) → обратное преобразование Latin-1 → UTF-8
- Осиротевшие события → удаление

 **E.3 Если .recover тоже падает (повреждены страницы данных)**

```
1. Bun.js rowid-scan: итерация по message/part с перехватом SQLITE_CORRUPT
2. sqlite3 .recover (может дать session metadata с других страниц)
3. Объединить результаты
4. Raw binary scan через Notepad++/rg (последний шанс)
```
