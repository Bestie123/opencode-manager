# Перенос сессий между проектами

> Doc-ID: S-MOVE-2 | Дата: 2026-06-02 | Связанные: [DB-SELECTOR-1], [H-TREE-1], [ARCH-v2], [REF-DATA-1]

## 1. Проблема

Кнопка «Перенести в проект» в оригинальной реализации обновляла только поле `directory`.
Но opencode идентифицирует сессии по `project_id`. Если `project_id` не обновлён —
сессия не отображается ни в старом, ни в новом проекте.

Дополнительно: дочерние subagent-сессии должны переноситься вместе с родителем.

## 2. Решение v2

### 2.1 Определение project_id

```python
def _resolve_project_id(self, directory: str) -> str:
    # 1. Получить git-корень выбранной директории
    git_root = subprocess.run(["git", "-C", directory, "rev-parse", "--show-toplevel"])
    
    # 2. Найти совпадение в таблице project
    for pid, wt in self.execute("SELECT id, worktree FROM project WHERE id != 'global'"):
        if git_root == wt:
            return pid
    
    # 3. Если не найден — 'global'
    return "global"
```

**Алгоритм:**
1. Выполнить `git -C <new_dir> rev-parse --show-toplevel`
2. Нормализовать путь (`replace("/", "\\")`)
3. Искать совпадение в `project` таблице
4. Если найден — использовать этот `project_id`
5. Если не найден — `'global'`

### 2.2 UPDATE трёх полей

```python
def update_session_directory(self, session_id: str, new_directory: str) -> bool:
    new_project_id = self._resolve_project_id(new_directory)
    new_path = new_directory.replace("\\", "/")  # forward slashes
    
    c.execute("""
        UPDATE session
        SET directory = ?, path = ?, project_id = ?
        WHERE id = ?
    """, (new_directory, new_path, new_project_id, session_id))
```

### 2.3 Каскад на subagent

```python
    # Все дочерние сессии тоже обновляются
    c.execute("""
        UPDATE session
        SET directory = ?, path = ?, project_id = ?
        WHERE parent_id = ?
    """, (new_directory, new_path, new_project_id, session_id))
```

## 3. GUI

### 3.1 Блокировка для subagent

```python
def _on_tree_select(self, event):
    has_subagent = any(
        self._session_map.get(iid) and self._session_map[iid].parent_id
        for iid in sel
    )
    self._move_btn.config(state=tk.DISABLED if has_subagent else tk.NORMAL)
```

### 3.2 Диалог при попытке перенести subagent

```python
messagebox.showwarning(
    "Дочерняя сессия",
    f"Это дочерняя (subagent) сессия.\n"
    f"Она привязана к родительской: «{pname}»\n\n"
    f"Переносите родительскую сессию —\n"
    f"дочерние переедут автоматически."
)
```

### 3.3 Нормализация путей Windows

```python
# tkinter.filedialog.askdirectory() на Windows возвращает forward slashes
# Исправление:
new_dir = new_dir.replace('/', '\\')
```

## 4. Защита от запущенного OpenCode

```python
if self._check_opencode():
    return
# → Get-Process -Name 'OpenCode.exe','opencode'
# → если найдены — блокировка
```

## 5. Сложности

### 5.1 Не-git директории
Если целевая папка не является git-репозиторием, `project_id` становится `'global'`.
Desktop-приложение может не показать такие сессии в боковой панели.

### 5.2 Desktop-совместимость
Desktop-приложение использует `.dat` файлы для UI-состояния.
После переноса сессия может не отобразиться, пока не обновлён `globalSync.project`.
**Решение:** Перезапустить Desktop.

### 5.3 Subagent orphan
Если родительская сессия была удалена, её дети становятся orphan'ами.
Перенос orphan невозможен — кнопка disabled (они не имеют parent_id в сессии).

### 5.4 Разные версии opencode
Разные версии opencode вычисляют `project_id` по-разному.
Если сессия перенесена в одной версии, а открывается в другой — `project_id` может не совпасть.

## 6. Пример

```sql
-- До переноса:
SELECT id, directory, path, project_id FROM session WHERE id = 'ses_...';
-- ses_... | Q:\Old\Project | Q:/Old/Project | global

-- После переноса:
-- ses_... | Q:\New\Project | Q:/New/Project | a8a2d42272ae...
```

## 7. SQL-запросы

```sql
-- Проверить, какие сессии имеют project_id, не совпадающий с project.worktree
SELECT s.id, s.project_id, p.worktree
FROM session s
JOIN project p ON s.project_id = p.id
WHERE s.directory NOT LIKE p.worktree || '%';

-- Найти все корневые сессии, которые можно перенести
SELECT id, title, directory FROM session WHERE parent_id IS NULL;
```
