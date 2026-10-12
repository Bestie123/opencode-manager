# Диагностика и решение проблем

> Doc-ID: GUIDE-TROUBLE-1 | Дата: 2026-06-22 | Связанные: [REF-SQL-1], [REF-DATA-1], [BUGS], [GUIDE-CORRUPT-1], [GUIDE-CORRUPT-2], [MCP-DB-001]

## 1. Сессия не отображается в opencode

### 1.1 Не та база данных
**Симптом:** В менеджере 60 сессий, а в opencode — 3.
**Диагностика:**
```bash
opencode db path
# → C:\Users\...\opencode-dev.db  (dev-версия использует свою БД)
```
**Решение:** Переключите БД в менеджере через селектор в тулбаре.
Если opencode использует dev-БД — скопируйте сессии миграцией (см. migration.md).

### 1.2 Сессия архивирована
**Симптом:** Сессия есть в менеджере, но не видна в opencode.
**Диагностика:**
```sql
SELECT id, title, time_archived FROM session WHERE id = '<session_id>';
```
**Решение:** Разархивируйте через менеджер (кнопка «Разархивировать»).

### 1.3 Некорректный project_id
**Симптом:** Сессия создана в проекте, но не отображается при его открытии.
**Диагностика:**
```sql
SELECT s.id, s.project_id, p.worktree FROM session s
LEFT JOIN project p ON s.project_id = p.id
WHERE s.id = '<session_id>';
```
**Решение:** Если project_id = 'global', а project.worktree указывает на другой проект — обновите project_id:
```sql
UPDATE session SET project_id = 'a8a2d42272aeac95b2502345313a1f1866da532a'
WHERE id = '<session_id>';
```

### 1.4 Слэши в directory (Windows)
**Симптом:** Сессия есть в БД, но не отображается в Desktop.
**Диагностика:**
```sql
SELECT id, directory, path FROM session WHERE directory LIKE '%/%';
```
**Решение:** Исправьте в менеджере через кнопку «Перенести в проект» (даже если в ту же папку — он нормализует пути).

## 2. OpenCode не запускается

### 2.1 Ошибка Tauri Store (BOM)
**Симптом:** Desktop не открывается, в renderer.log ошибка:
```
Error invoking remote method 'store-set': SyntaxError: Unexpected token 'ï»¿'
```
**Причина:** UTF-8 BOM в global.dat.
**Решение:**
```bash
# Сохранить файл без BOM
python -c "import json; f=open(r'%APPDATA%\ai.opencode.desktop\opencode.global.dat','r',encoding='utf-8-sig'); d=json.load(f); f.close(); open(r'%APPDATA%\ai.opencode.desktop\opencode.global.dat','w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False))"
```

### 2.2 БД заблокирована (SQLITE_BUSY)
**Симптом:** OpenCode пишет "database is locked".
**Причина:** Другой процесс (Desktop, менеджер, CLI) держит БД.
**Решение:** Закройте все процессы OpenCode. Если не помогает — удалите `-wal` и `-shm` файлы (данные не потеряются).

### 2.3 Порча БД (SQLITE_CORRUPT)
**Симптом:** Ошибка "database disk image is malformed".
**Причина:** Параллельная запись в SQLite (opencode CLI + Desktop + бекап).
**Решение:**
```bash
# Быстрое восстановление (если .recover работает)
sqlite3 opencode.db ".recover" > dump.sql
# Конвертация UTF-16 → UTF-8 (StreamReader, НЕ ReadAllText!)
# Импорт через cmd.exe (PowerShell < не работает)
cmd.exe /c "sqlite3.exe new.db < dump-utf8.sql"
```
**Два типа повреждения:**
1. **B-tree leaf pages** — `.recover` частичный (139/279), `sessions > 0`. См. [GUIDE-CORRUPT-1]
2. **sqlite_master / header** — `.recover` полный (148/148), `sessions = -1`. См. [GUIDE-CORRUPT-2] (приложение D)

## 3. Проблемы с менеджером

### 3.1 Не запускается (ImportError)
**Симптом:** `ModuleNotFoundError: No module named 'core'`.
**Решение:** Запускайте из корня проектам:
```bash
cd Q:\User_Data\Desktop\opencode-manager
python run.py
```

### 3.2 Ошибка при чтении БД
**Симптом:** `sqlite3.OperationalError: database is locked`.
**Решение:** Закройте OpenCode (Desktop + CLI) перед запуском менеджера.

### 3.3 Кнопки «Архивировать»/«Удалить» неактивны
**Причина:** OpenCode запущен. Менеджер блокирует операции записи.
**Решение:** Закройте OpenCode (Desktop + все процессы).

### 3.4 Сессии не обновляются после операции
**Решение:** Нажмите «Обновить» (F5) или переключитесь на другую вкладку и обратно.

## 4. Проблемы с MCP

### 4.1 MCP не загружается
**Симптом:** MCP-инструменты не видны после перезапуска opencode.
**Диагностика:**
```bash
opencode debug config | grep -A5 mcp-opencode-db
```
**Решение:**
1. Убедитесь что путь к python.exe — ПОЛНЫЙ (не `"python"`, а `"C:\...\python.exe"`)
2. Установите пакет: `pip install mcp`
3. Проверьте JSONC-синтаксис конфига

### 4.2 MCP отвечает ошибкой
**Симптом:** `Error: no such column: ...`
**Решение:** Проверьте что таблица существует в целевой БД. Версии opencode могут иметь разные схемы.

## 5. Проблемы с проектами

### 5.1 Дубликат project_id для одного каталога
**Симптом:** Один каталог имеет несколько project_id в таблице project.
**Причина:** Разные версии opencode вычисляют хеш по-разному.
**Решение:** Удалите дубликаты:
```sql
DELETE FROM project WHERE id IN (
  SELECT id FROM project WHERE worktree = 'Q:\...\opencode-manager'
  ORDER BY time_created DESC
  LIMIT -1 OFFSET 1
);
```

### 5.2 Сессии с project_id='global' не показываются
**Причина:** project_id='global' — legacy-значение. Современный opencode ожидает хеш.
**Решение:** Если project.worktree указывает на нужную директорию — opencode должен показать сессии.
Если нет — используйте кнопку «Перенести в проект».

## 6. Производительность

### 6.1 OpenCode тормозит при загрузке сессии
**Диагностика:** Проверьте размер reasoning:
```sql
SELECT ROUND(SUM(CASE WHEN json_extract(data,'$.type')='reasoning' THEN LENGTH(data) ELSE 0 END)/1048576.0,1) AS mb
FROM part WHERE session_id = '<session_id>';
```
**Решение:** Удалите reasoning через вкладку «Сообщения» → «Удалить reasoning».

### 6.2 БД выросла до 500+ МБ
**Решение:**
1. Strip Reasoning (все) — экономия ~77%
2. Удалить subagent — убрать вложенные
3. Очистить осиротевшие diff'ы
4. Vacuum

### 6.3 Vacuum требует слишком много места
**Решение:** Если на диске <2× размера БД — не делайте Vacuum.
Вместо этого:
```sql
PRAGMA auto_vacuum = 1;
VACUUM;
```
Или используйте опцию `--vacuum` с запасом места.

## 7. Desktop-приложение

### 7.1 Desktop не видит новые сессии
**Решение:** Перезапустите Desktop (полностью закройте, не сворачивайте в трей).

### 7.2 Desktop показывает старые (закэшированные) сессии
**Решение:**
1. Закройте Desktop
2. Удалите `%APPDATA%\ai.opencode.desktop\Cache\` (кэш Chromium)
3. Запустите Desktop заново

### 7.3 Desktop пишет "Session not found"
**Причина:** Сессия была в opencode.db, но Desktop использует opencode-dev.db.
**Решение:** Скопируйте сессии между БД (см. migration.md).

## 8. Ошибки JSON.parse при загрузке сессий/сообщений

### 8.1 "Не удалось загрузить сессии" — пустые сессии
**Симптом:** Toast "Не удалось загрузить сессии для {project}". В консоли: "Failed to load sessions".
**Причина:** Сессии без единого сообщения. opencode не может их обработать.
**Диагностика:**
```sql
SELECT COUNT(*) FROM session s LEFT JOIN message m ON m.session_id = s.id WHERE m.id IS NULL;
```
**Решение:** `db_fix()` в MCP `opencode-db`, или вручную:
```sql
DELETE FROM session WHERE id IN (
  SELECT s.id FROM session s LEFT JOIN message m ON m.session_id = s.id WHERE m.id IS NULL
);
```

### 8.2 500 при загрузке — пустые sandboxes в project
**Симптом:** "Unexpected server error" при открытии любой сессии. В логах: `SyntaxError: Unexpected end of JSON input`.
**Цепочка:** `SELECT ... FROM project WHERE id = ?` → Drizzle `mode:"json"` → `JSON.parse("")` → SyntaxError.
**Причина:** У проекта `global` поле `sandboxes = ""` вместо `"[]"`.
**Диагностика:**
```sql
SELECT id, sandboxes FROM project WHERE sandboxes = '' OR sandboxes IS NULL;
```
**Решение:** `db_fix()` в MCP `opencode-db`, или вручную:
```sql
UPDATE project SET sandboxes = '[]' WHERE sandboxes = '' OR sandboxes IS NULL;
```

### 8.3 500 при fetchMessages — пустые message.data / part.data
**Симптом:** "Unexpected server error" при загрузке сообщений сессии. Ошибка в `fetchMessages`.
**Цепочка:** `SELECT ... FROM message WHERE session_id = ?` → Drizzle `mode:"json"` → `JSON.parse("")` → SyntaxError.
**Причина:** Сообщения с пустым `data` (вероятно, от прерванной записи).
**Диагностика:**
```sql
SELECT COUNT(*) FROM message WHERE data = '' OR data IS NULL OR json_valid(data) = 0;
SELECT COUNT(*) FROM part WHERE data = '' OR data IS NULL OR json_valid(data) = 0;
```
**Решение:** `db_fix()` в MCP `opencode-db`, или вручную:
```sql
UPDATE message SET data = '{"role":"assistant"}' WHERE data = '' OR data IS NULL;
DELETE FROM part WHERE data = '' OR data IS NULL OR json_valid(data) = 0;
```

### 8.4 Битая кириллица (mojibake) в заголовках и тексте

**Симптом (тип 1 — Latin1):** `Ð—Ð°Ð´Ð°ÑÐ¸ Ð¸Ð· Ð´Ð¾ÐºÑÐ¼ÐµÐ½ÑÐ°ÑÐ¸Ð¸`
**Симптом (тип 2 — CP866):** `╨Ч╨░╨┤╨░╤З╨╕ ╨╕╨╖ ╨┤╨╛╨║╤Г╨╝╨╡╨╜╤В╨░╤Ж╨╕╨╕`

**Причина:** UTF-8 байты русского текста были прочитаны как другая однобайтовая кодировка:
- **Latin1-тип:** UTF-8 → Latin1 (Windows-1252) → сохранено как Unicode
- **CP866-тип:** UTF-8 → CP866 (DOS Cyrillic) → сохранено как Unicode

**Фикс Latin1-типа (TypeScript/Bun):**
```typescript
const fixed = Buffer.from(mojibakeText, "latin1").toString("utf-8")
```
**Фикс Latin1-типа (Python):**
```python
fixed = mojibake_text.encode("latin1").decode("utf-8")
```

**Фикс CP866-типа:** Используйте MCP-инструмент `oc_fix_mojibake` (работает через полную CP866 lookup-таблицу). Подробнее: [FIX-ENCODING-1] (docs/features/fix-encoding.md), [REF-ENCODING-1] (docs/reference/encoding.md).

**Детекция в MCP:**
```json
// Сканирование всех session.title на mojibake
oc_detect_mojibake({ "db": "opencode.db" })

// Предпросмотр исправления
oc_fix_mojibake({ "db": "opencode.db", "dry_run": true })

// Реальное исправление (создаёт backup)
oc_fix_mojibake({ "db": "opencode.db", "confirm": true })
```

**Визуальные признаки:**
| Тип | Символы |
|-----|---------|
| Latin1 | `Ð Ñ Ò Ó Ô Õ Ö × Ø Ù Ú Û Ü Ý Þ ß` |
| CP866 | `╨ ╤ ╥ ╗ ║ ╔ ╚ ╝ ═ ╬ ╩ ╦ ╠ ╣` |

### 8.5 Осиротевшие события (events без event_sequence)
**Симптом:** Медленная загрузка, раздутая БД (до 40K+ лишних записей).
**Причина:** События event-sourcing остаются после удаления сессии/сообщения, если каскадный DELETE не сработал.
**Диагностика:**
```sql
SELECT COUNT(*) FROM event e LEFT JOIN event_sequence es ON es.aggregate_id = e.aggregate_id WHERE es.aggregate_id IS NULL;
```
**Решение:** `db_fix()` в MCP `opencode-db`.

## 9. Профилактика

### 9.1 Автоматическая проверка
Используйте MCP `opencode-db.db_health()` — одна команда проверяет все известные проблемы:
- Пустые сессии
- Битый JSON в message/part
- Пустые sandboxes
- Битую кодировку
- Осиротевшие события
- Lost & found

### 9.2 Регулярные проверки
```bash
# Через opencode MCP (в сессии):
db_health()                    # быстрая проверка
db_find_broken()               # полное сканирование
db_fix(dry_run=true)           # что будет исправлено
db_fix(fix_encoding=true)      # полная починка + encoding
```

### 9.3 После переноса данных между БД
Всегда запускайте после `INSERT ... SELECT` или ручного копирования:
```bash
db_fix(fix_encoding=true)      # чинит и JSON, и кодировку
```
