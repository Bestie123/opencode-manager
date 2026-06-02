# Диагностика и решение проблем

> Doc-ID: GUIDE-TROUBLE-1 | Дата: 2026-06-02 | Связанные: [REF-SQL-1], [REF-DATA-1], [BUGS]

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
**Решение:**
```bash
# Создать дамп и восстановить
sqlite3 opencode.db ".dump" > dump.sql
sqlite3 opencode_new.db < dump.sql
# Если дамп тоже падает — последний шанс:
sqlite3 opencode.db "PRAGMA integrity_check;"
```

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
