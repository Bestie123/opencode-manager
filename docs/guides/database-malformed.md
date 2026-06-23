# Doc-ID: DB-MALFORMED-1 | Дата: 2026-06-22 | Связанные: [MCP-OCDB-2], [ARCH-v2]

# Database "disk image is malformed" — Диагностика и восстановление

## Симптомы

```
Error: database disk image is malformed
```

opencode не запускается, GUI показывает пустой список сессий, или конкретная база данных не открывается.

## Корневая причина

**WAL/SHM конфликт при замене .db файла.**

Когда вы заменяете `opencode.db` файлом из другого источника (бэкап, копия, другая версия), рядом остаются `-wal` и `-shm` файлы от **старой** базы. SQLite при открытии пытает replay WAL-журнала от схемы v47 поверх базы схемы v39 → крашится.

```
opencode.db       (schema v39 — старая)     ← новый файл
opencode.db-wal   (schema v47 — от старой)  ← СТАРЫЙ WAL
opencode.db-shm   (schema v47 — от старой)  ← СТАРЫЙ SHM
```

SQLite видит WAL с неподходящей checksum → `PRAGMA integrity_check` падает → "malformed".

## Диагностика

### Через MCP-сервер

```python
# Проверить ВСЕ базы
oc_health_check()

# Проверить конкретную базу
oc_health_check(db="opencode.db")
```

### Через opencode-manager

```python
from core import OpenCodeDB

report = OpenCodeDB.check_db_health(r"C:\Users\misch\.local\share\opencode\opencode.db")
print(report.summary())
```

### Через Python/SQLite

```python
import sqlite3

conn = sqlite3.connect("opencode.db", timeout=5)
c = conn.cursor()
c.execute("PRAGMA integrity_check")
print(c.fetchone())  # ok или сообщение об ошибке

c.execute("PRAGMA schema_version")
print(f"Schema version: {c.fetchone()[0]}")

c.execute("SELECT COUNT(*) FROM session")
print(f"Sessions: {c.fetchone()[0]}")
```

## Решения

### 1. Удалить WAL/SHM (самое частое решение)

```powershell
# Остановите opencode перед этим!
Remove-Item "C:\Users\misch\.local\share\opencode\opencode.db-wal" -Force
Remove-Item "C:\Users\misch\.local\share\opencode\opencode.db-shm" -Force
```

### 2. Через MCP oc_repair_wal

```python
# Просмотр что будет удалено
oc_repair_wal(db="opencode.db", confirm=true, dry_run=true)

# Выполнить ремонт
oc_repair_wal(db="opencode.db", confirm=true)
```

### 3. Восстановление из бэкапа

```powershell
# Бэкапы хранятся в:
Get-ChildItem "C:\Users\misch\.local\share\opencode\backups\"

# Восстановить конкретный бэкап
Copy-Item "backups\opencode_pre_merge_20260622_192859.db" "opencode.db" -Force
Remove-Item "opencode.db-wal" -Force -ErrorAction SilentlyContinue
Remove-Item "opencode.db-shm" -Force -ErrorAction SilentlyContinue
```

### 4. Извлечение данных из повреждённой базы

```powershell
# Если sqlite3 CLI доступен:
sqlite3 corrupted.db .dump > recovered.sql
sqlite3 new_opencode.db < recovered.sql

# Если sqlite3 CLI недоступен — через Python:
import sqlite3
src = sqlite3.connect("corrupted.db", timeout=5)
dst = sqlite3.connect("recovered.db", timeout=5)

# Скопировать схему
for sql in src.execute("SELECT sql FROM sqlite_master WHERE sql IS NOT NULL"):
    try: dst.execute(sql[0])
    except: pass

# Скопировать данные по таблицам
for table in ["session", "message", "part", "todo"]:
    rows = src.execute(f"SELECT * FROM {table}").fetchall()
    if rows:
        cols = [d[0] for d in src.execute(f"SELECT * FROM {table}").description]
        placeholders = ",".join(["?" for _ in cols])
        dst.executemany(f"INSERT OR IGNORE INTO {table} VALUES({placeholders})", rows)
        print(f"  Copied {len(rows)} rows from {table}")

dst.commit()
dst.close()
```

### 5. Автоматическое восстановление (opencode >=最新)

Начиная с определённой версии, opencode автоматически:
1. Проверяет `PRAGMA integrity_check` при открытии
2. Если проверка не прошла и WAL/SHM существуют — удаляет их
3. Повторяет попытку открытия

Это поведение добавлено в `packages/core/src/database/database.ts` (функция `validateDatabaseFile`).

## Схемы баз данных

| Версия | schema_version | Ключевые отличия |
|--------|---------------|-----------------|
| v39 | 39 | Нет `credential`, нет `project_directory.strategy` |
| v47 | 47 | Есть `credential`, `project_directory.strategy` |
| v49 | 49 | Аналогична v47 + дополнительные миграции |

opencode может работать со старыми схемами — миграции apply автоматически. Но WAL-файлы от v47 НЕ совместимы с v39.

## Профилактика

1. **Никогда не заменяйте .db файл напрямую** без удаления WAL/SHM
2. **Используйте opencode-manager** для слияния сессий вместо замены файлов
3. **Делайте бэкапы** перед миграциями: `oc_backup_db()`
4. **Проверяйте здоровье** после замены: `oc_health_check()`

## Структура файлов

```
~/.local/share/opencode/
├── opencode.db          # Основная база (WAL mode)
├── opencode.db-wal      # Write-Ahead Logging журнал
├── opencode.db-shm      # Shared memory файл для WAL
├── opencode-local.db    # Локальная база (без сессий)
├── 0001-err.db          # Старая копия (schema v39)
├── 0002-err.db          # Старая копия (schema v49)
├── 0003-err.db          # Повреждена (malformed)
├── 0004-.db             # Повреждена (malformed)
├── backups/             # Бэкапы
│   ├── opencode_pre_merge_*.db
│   └── pre_restore_*/
└── storage/
    └── session_diff/    # JSON-диффы сессий
```

## Известные проблемы

### mojibake в названиях сессий
Сессии с кириллицей могут отображаться как `???`. Используйте `oc_fix_mojibake(confirm=true)`.

### Потеря сессий при копировании
При копировании .db файла через `Copy-Item` теряются сессии, созданные после момента копирования. Используйте `oc_merge_sessions()` вместо замены файлов.

### WAL-файл растёт бесконечно
Если opencode не выполняет `PRAGMA wal_checkpoint(TRUNCATE)`, WAL-файл может вырасти до гигабайтов. Решение: перезапуск opencode или `PRAGMA wal_checkpoint(TRUNCATE)` через sqlite3 CLI.
