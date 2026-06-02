# Выбор базы данных (DB Selector)

> Doc-ID: DB-SELECTOR-1 | Дата: 2026-06-02 | Связанные: [ARCH-v2], [S-MOVE-2], [REF-SQL-1], [GUIDE-MIGRATE-1]

## 1. Проблема

У пользователя может быть несколько SQLite-баз OpenCode. Причины:
- Установлены разные каналы (stable + dev)
- Системные копии (Windows Shadow Copy)
- Ручные бэкапы
- Предыдущие версии opencode

Менеджер должен уметь работать с любой из них.

## 2. Алгоритм автоопределения

### 2.1 Сканирование

```python
@staticmethod
def list_databases() -> list[tuple[str, str, int]]:
    base = Path.home() / ".local" / "share" / "opencode"
    for f in base.glob("opencode*.db"):
        # Фильтр: .db-shm, .db-wal, .backup-*
        if name.endswith(".db-shm") or name.endswith(".db-wal"):
            continue
        if name.endswith(".backup-"):
            continue
        conn = sqlite3.connect(str(f))
        count = conn.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        candidates.append((str(f), label, count))
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates
```

### 2.2 Выбор основной БД

```python
def _detect_db_path() -> Optional[str]:
    dbs = list_databases()
    if dbs:
        return dbs[0][0]  # БД с максимальным числом сессий
```

### 2.3 Приоритет при равном количестве

Если несколько БД имеют одинаковое количество сессий:
1. Выбирается `opencode.db` (как основная)
2. Если `opencode.db` нет — первая по алфавиту

## 3. GUI

### 3.1 Селектор

Выпадающий список в тулбаре вкладки «Сессии»:
```
[opencode (60 сес.)] [Сортировка...] [Поиск...] [Sub] [Обновить]
```

### 3.2 Переключение

```python
def _switch_db(self, idx: int):
    path, label, _ = self._db_list[idx]
    self.db = OpenCodeDB(path)
    self.cli = OpenCodeCLI(db_path=path)
    self._load_sessions()
    self.status_var.set(f"БД: {label} ({path})")
```

### 3.3 Статус-бар

Показывает активную БД:
```
[opencode] Загружено 60 сессий  |  БД: opencode (60 сес.)
```

## 4. Типы БД

| Имя в списке | Файл | Типичное число сессий | Происхождение |
|---|---|---|---|
| `opencode` | `opencode.db` | 56-60 | Desktop / stable CLI |
| `opencode-dev` | `opencode-dev.db` | 3-7 | Dev-сборка npm |
| `opencode1` | `opencode1.db` | ~51 | Ручная копия |
| `opencode - копия` | `opencode - копия.db` | ~51 | Shadow Copy |
| `opencode - копия (2)` | `opencode - копия (2).db` | ~51 | Ещё одна копия |

## 5. Сложности

### 5.1 PowerShell-обёртка

На Windows `opencode` — это `.ps1` скрипт, не exe-файл.
`subprocess.run("opencode")` падает с `FileNotFoundError`.
**Решение:** Вызывать через `powershell -NoProfile -Command "opencode db path"`.

### 5.2 WAL-блокировка

Если Desktop-приложение запущено, WAL-файлы могут блокировать чтение.
**Решение:** `sqlite3.connect(path, timeout=10)` — таймаут 10 сек.

### 5.3 Копии БД

`glob("opencode*.db")` находит также `opencode1.db`, `opencode - копия.db`.
**Решение:** Не фильтровать — показать все. Пользователь сам выбирает нужную.

### 5.4 Desktop overwrite

Если Desktop запущен и пишет `global.dat`, мои изменения `globalSync.project`
могут быть перезаписаны. **Решение:** Закрыть Desktop перед редактированием.

## 6. Примеры использования

### 6.1 Я не вижу свои сессии

1. Проверьте статус-бар: какая БД активна?
2. Если `opencode-dev` — переключитесь на `opencode`
3. Если не помогло — проверьте `opencode db path` в терминале

### 6.2 Хочу перенести сессию из dev в stable

1. Выберите `opencode-dev` в селекторе
2. Экспортируйте нужные сессии
3. Переключитесь на `opencode`
4. Импортируйте сохранённые JSON

## 7. SQL-запросы

```sql
-- Найти все БД в директории
SELECT 'opencode.db' AS db
UNION ALL SELECT 'opencode-dev.db'
UNION ALL SELECT 'opencode1.db';

-- Сравнить количество сессий
SELECT 'opencode.db', COUNT(*) FROM main.session
UNION ALL
SELECT 'opencode-dev.db', COUNT(*) FROM main.session;
```
