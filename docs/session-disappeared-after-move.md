# Сессии пропали после "Перенести в проект" — расследование и修复

## Проблема

После использования функции "Перенести в проект" в OpenCode Session Manager (`opencode-manager`) сессия перестала отображаться в OpenCode Desktop. После попытки исправления — перестали отображаться ВСЕ сессии.

## Диагностика

### 1. Две разные базы данных

В системе оказалось ДВЕ SQLite-базы OpenCode:

| Файл | Сессий | Используется |
|------|--------|-------------|
| `opencode.db` | 56 | Desktop-приложение (канал `latest`) |
| `opencode-dev.db` | 5 | CLI/TUI (канал `dev`) |

OpenCode определяет базу по каналу установки (`InstallationChannel`):
```typescript
// packages/core/src/database/database.ts
if (["latest", "beta", "prod"].includes(InstallationChannel))
    return join(Global.Path.data, "opencode.db")
return join(Global.Path.data, `opencode-${InstallationChannel}.db`)
```

Desktop-приложение (v1.15.13) использует канал `latest` → `opencode.db`.
CLI из npm использует канал `dev` → `opencode-dev.db`.

**Ошибка:** В предыдущей сессии было изменено автоопределение БД в `core.py`, из-за чего Session Manager начал показывать `opencode-dev.db` (5 сессий) вместо `opencode.db` (56 сессий).

### 2. Повреждение глобального проекта

В `opencode-dev.db` таблица `project` содержала некорректные данные для глобального проекта:

```
id=global  worktree='Q:\User_Data\Desktop\opencode-manager'  vcs=git
```

Должно быть:

```
id=global  worktree='/'  vcs=NULL
```

Из-за этого OpenCode не мог правильно определить контекст проекта и отфильтровать сессии.

### 3. Повреждение сессии

Сессия `ses_183b55eb1ffefsEyN3kiarjDH2` была повреждена после "переноса":
- `directory` содержал forward slashes (`Q:/...`) вместо backslashes (`Q:\...`)
- `path` был пустым
- `project_id` не обновился (остался от TestQA)

### 4. Алфавитная сортировка копий БД

Функция `_detect_db_path()` искала файлы по glob `opencode*.db` и выбирала БД с наибольшим числом сессий. При равенстве (56 сессий) сортировка выбирала **копию** `opencode - Копия (2).db` по алфавиту вместо `opencode.db`.

### 5. Повреждение state-файлов Desktop-приложения

Прямое редактирование `opencode.global.dat` (Tauri Store) через Python привело к:
- Некорректной JSON структуре (мусорные байты, BOM)
- Tauri Store не мог распарсить файл → ошибка `store-set`
- Рендерер Desktop-приложения не загружался → пустое окно

Лог ошибки:
```
renderer.log: Uncaught (in promise) Error: Error invoking remote method 'store-set':
SyntaxError: Unexpected token '�>�', "�>�{"prompt-"... is not valid JSON
```

State-файлы Desktop-приложения находятся в `%APPDATA%\ai.opencode.desktop\`:
- `opencode.global.dat` — основное состояние (кэш проектов, настроек)
- `opencode.workspace.*.dat` — per-workspace данные
- `default.dat` — кэш настроек

## Исправления

### Fix 1: `_detect_db_path` — приоритет `opencode.db` (core.py)

```python
# Было: сортировка по количеству сессий, при равенстве — по алфавиту
# Стало: при равенстве сессий выбирать opencode.db
if candidates:
    candidates.sort(key=lambda x: (-x[1], x[0]))
    best = candidates[0]
    for path, count in candidates:
        if count < best[1]:
            break
        if os.path.basename(path).lower() == "opencode.db":
            best = (path, count)
    return best[0]
```

### Fix 2: Глобальный проект (SQLite)

```sql
UPDATE project SET worktree = '/', vcs = NULL WHERE id = 'global';
```

### Fix 3: Коррумпированная сессия (SQLite)

```sql
UPDATE session 
SET directory = REPLACE(directory, '/', '\'),
    path = REPLACE(directory, '/', '/'),
    project_id = 'global'
WHERE id = 'ses_183b55eb1ffefsEyN3kiarjDH2';
```

### Fix 4: State-файлы Desktop-приложения

Удаление повреждённых `.dat` файлов и перезапись `opencode.global.dat`:
- Убрать BOM (UTF-8 signature) — Tauri Store не понимает BOM
- Проверить валидность JSON
- Восстановить ключи `globalSync.project`, `layout.page` из бэкапа

## Уроки

1. **Не редактировать state-файлы Desktop-приложения напрямую.** Tauri Store имеет свой формат, редактирование через Python/текстовый редактор приводит к повреждению.
2. **Различать базы данных.** Desktop и CLI могут использовать разные БД. Проверять через `opencode db path`.
3. **Автоопределение БД — источник багов.** Лучше использовать явный путь.
4. **Резервное копирование.** Перед любыми операциями с БД — создавать backup.
5. **Перезапуск приложения.** После изменения БД — полностью закрыть и перезапустить OpenCode.

## Файловая структура Desktop-приложения

| Путь | Назначение |
|------|-----------|
| `%APPDATA%\ai.opencode.desktop\opencode.global.dat` | Tauri Store — основное состояние |
| `%APPDATA%\ai.opencode.desktop\opencode.workspace.*.dat` | Per-workspace данные |
| `%APPDATA%\ai.opencode.desktop\default.dat` | Кэш настроек |
| `%APPDATA%\ai.opencode.desktop\logs\` | Логи приложения |
| `%LOCALAPPDATA%\ai.opencode.desktop\opencode\` | Данные sidecar (locks) |
| `%LOCALAPPDATA%\Programs\@opencode-aidesktop\OpenCode.exe` | Исполняемый файл Desktop-приложения |
