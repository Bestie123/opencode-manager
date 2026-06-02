# Иерархическое дерево сессий

> Doc-ID: H-TREE-1 | Дата: 2026-06-02 | Связанные: [DB-SELECTOR-1], [ARCH-v2], [REF-DATA-1], [ARCHIVE-1]

## 1. Проблема

opencode скрывает subagent-сессии из основного списка — они видны только внутри родителя.
Пользователь не может увидеть полную картину: какие subagent-сессии существуют, сколько их,
к какому родителю они привязаны. Менеджер должен показывать ВСЕ сессии с иерархией.

## 2. Решение

### 2.1 Treeview column #0

Вместо `show="headings"` (плоская таблица) используется `show="tree headings"`.
Column #0 отображает название сессии с отступами иерархии.

```python
columns = ("children", "status", "directory", "size", "messages", ...)
self.tree = ttk.Treeview(..., show="tree headings", ...)
self.tree.heading("#0", text="Название", ...)
```

### 2.2 Построение parent→children map

```python
def _refresh_tree(self):
    children_of: dict[str, list[SessionInfo]] = {}
    orphans = []
    roots = []
    
    for s in self._sessions:
        if s.parent_id:
            if s.parent_id in existing_ids:
                children_of.setdefault(s.parent_id, []).append(s)
            else:
                orphans.append(s)  # родитель удалён
        else:
            roots.append(s)
    
    # Сначала корневые
    for s in roots:
        child_count = len(children_of.get(s.id, []))
        iid = self._insert_session(s, child_count=child_count)
        # Потом дети
        for child in children_of.get(s.id, []):
            self._insert_session(child, parent_iid=iid)
        if child_count:
            self.tree.item(iid, open=True)  # раскрыть по умолчанию
    
    # В конце orphan-секция
    if orphans:
        sep_iid = self.tree.insert("", tk.END, text="── Orphan ──", ...)
        for child in orphans:
            self._insert_session(child, orphan=True)
```

### 2.3 Вставка строки

```python
def _insert_session(self, s, parent_iid="", child_count=0, orphan=False):
    children_display = "⚠" if orphan else "→" if parent_iid else str(child_count) or ""
    status_display = "🗄" if s.is_archived else ""
    iid = self.tree.insert(parent_iid, tk.END, text=s.title[:60], values=(
        children_display, status_display, directory, ...
    ))
    # Теги для стилизации
    if orphan:
        self.tree.item(iid, tags=("orphan_subagent",))
    elif parent_iid and s.is_archived:
        self.tree.item(iid, tags=("archived_subagent",))
    elif parent_iid:
        self.tree.item(iid, tags=("subagent",))
    elif s.is_archived:
        self.tree.item(iid, tags=("archived",))
    return iid
```

## 3. Колонки

| Колонка | Содержимое | Ширина |
|----------|-----------|--------|
| #0 (Название) | Заголовок сессии с отступом | 280px |
| Дочер. | Число / → / ⚠ | 55px |
| Сост. | 🗄 / пусто | 55px |
| Директория | Сокращённый путь | 260px |
| Размер | N.N MB / GB | 80px |
| Сообщ. | N | 70px |
| Tokens In | N | 90px |
| Tokens Out | N | 90px |
| Reasoning | N | 90px |
| Возраст | today / Nd / Nw | 70px |
| Модель | provider/model | 120px |

## 4. Типы строк

| Тип | Цвет | Шрифт | Колонка Дочер. |
|-----|------|-------|----------------|
| Корневая | обычный | обычный | число детей |
| Subagent | #8b949e / #6b7280 | курсив | → |
| Orphan | #dc2626 / #f85149 | обычный | ⚠ |
| Архивная корневая | #8b949e / #9ca3af | обычный | число |
| Архивный subagent | #6b7280 / #9ca3af | курсив | → |

## 5. Чекбокс «Sub»

В тулбаре. При выключении subagent-сессии не отображаются в дереве.

```python
self.subagent_var = tk.BooleanVar(value=True)
ttk.Checkbutton(toolbar, text="Sub", variable=self.subagent_var, command=self._refresh_tree)
```

## 6. Поиск с родителем

Если поиск совпал с subagent — его родитель тоже включается в результат:

```python
def _filter_sessions(self, *args):
    search = self.search_var.get().lower()
    if not search:
        self._sessions = self._all_sessions
    else:
        matched = [s for s in self._all_sessions if search in s.title.lower() or search in s.id.lower()]
        child_parent_ids = {s.parent_id for s in matched if s.parent_id}
        for s in self._all_sessions:
            if s.id in child_parent_ids and s not in matched:
                matched.append(s)
        self._sessions = matched
```

## 7. Блокировка «Перенести в проект» для subagent

```python
def _on_tree_select(self, event):
    for iid in sel:
        s = self._session_map.get(iid)
        if s and s.parent_id:
            has_subagent = True
    self._move_btn.config(state=tk.DISABLED if has_subagent else tk.NORMAL)
```

При клике на subagent — диалог:
> «Это дочерняя сессия. Переносите родительскую — дети переедут автоматически.»

## 8. Сложности

### 8.1 Orphan без родителя
Если родитель удалён, дети становятся orphan'ами.
Показаны в отдельной секции в конце дерева красным цветом.

### 8.2 Согласованность с поиском
Если родитель не совпал по поиску, но совпал ребёнок — родитель всё равно показывается.

### 8.3 Производительность
Для БД с 60+ сессиями и 36+ subagent построение дерева занимает <10ms.
Если сессий >5000 — стоит добавить lazy-построение.

## 9. Пример

```python
# Дано:
#  Корневые: ses_A, ses_B
#  Subagent: ses_C → ses_A, ses_D → ses_A, ses_E → ses_B
#  Orphan: ses_F (родитель удалён)

# Результат в дереве:
# ses_A (3 детей)
#   ├─ ses_C
#   ├─ ses_D
# ses_B (1 ребёнок)
#   └─ ses_E
# ── Orphan (родитель удалён) ──
#   ses_F ⚠
```
