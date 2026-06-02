# Модель данных сессий OpenCode

> Doc-ID: REF-DATA-1 | Дата: 2026-06-02 | Связанные: [REF-SQL-1], [ARCH-v2]

## 1. Общая схема

```
┌──────────┐     ┌──────────┐     ┌──────────┐
│  session ├─────┤ message  ├─────┤   part   │
└──────────┘     └──────────┘     └──────────┘
     │
     │ 1:N
     ▼
┌──────────┐     ┌─────────────────────┐
│   todo   │     │  session_diff/*.json│ (внешний файл)
└──────────┘     └─────────────────────┘
```

## 2. Таблица session

### 2.1 Колонки

| Колонка | Тип | Обязательный | Описание |
|----------|-----|-------------|----------|
| `id` | TEXT PK | да | Уникальный ID вида `ses_<random>`. |
| `project_id` | TEXT FK NOT NULL | да | ID проекта (из таблицы project) или `'global'`. |
| `parent_id` | TEXT? | нет | Для subagent — ID родительской сессии. NULL для корневых. |
| `slug` | TEXT NOT NULL | да | Короткий читаемый идентификатор (`quick-guide`). |
| `directory` | TEXT NOT NULL | да | Путь к проекту на момент создания сессии (`Q:\...\project`). |
| `path` | TEXT? | нет | Тот же путь с forward slashes. |
| `title` | TEXT NOT NULL | да | Название сессии (первый промпт или автоназвание). |
| `version` | TEXT NOT NULL | да | Версия формата ( `'local'` ). |
| `model` | TEXT? | нет | JSON с modelID, providerID, variant. |
| `agent` | TEXT? | нет | Имя агента (`'build'`, `'explore'`, `'general'`). |
| `time_created` | INTEGER NOT NULL | да | Timestamp создания (ms). |
| `time_updated` | INTEGER NOT NULL | да | Timestamp последнего обновления (ms). |
| `time_compacting` | INTEGER? | нет | Timestamp последней компактации. |
| `time_archived` | INTEGER? | нет | Timestamp архивации. NULL = активна. |
| `tokens_input` | INTEGER | да | Сумма входящих токенов. |
| `tokens_output` | INTEGER | да | Сумма исходящих токенов. |
| `tokens_reasoning` | INTEGER | да | Сумма reasoning-токенов. |
| `tokens_cache_read` | INTEGER | да | Прочитано из кэша. |
| `tokens_cache_write` | INTEGER | да | Записано в кэш. |
| `cost` | REAL | да | Стоимость сессии в $ (0 для free-моделей). |
| `metadata` | TEXT? | нет | Дополнительные метаданные (JSON). |

### 2.2 Типы сессий

| Тип | parent_id | project_id | Описание |
|-----|-----------|------------|----------|
| **Корневая** | NULL | хеш или 'global' | Создана пользователем. Отображается в списке сессий. |
| **Subagent** | ID родителя | как у родителя | Создана @explore/@general. Скрыта из списка, видна только внутри родителя. |
| **Orphan** | ID удалённого родителя | любой | Subagent, чей родитель был удалён. Не привязана ни к чему. |
| **Архивная** | любой | любой | time_archived IS NOT NULL. Скрыта из opencode. |

## 3. Таблица message

| Колонка | Тип | Описание |
|----------|-----|----------|
| `id` | TEXT PK | Уникальный ID (`msg_...`). |
| `session_id` | TEXT FK NOT NULL | Связь с session. |
| `time_created` | INTEGER NOT NULL | Timestamp (ms). |
| `time_updated` | INTEGER NOT NULL | Timestamp обновления. |
| `data` | TEXT (JSON) | Основные данные сообщения. |

### 3.1 Структура `data` (JSON)

```json
{
  "role": "user" | "assistant",
  "summary": {
    "diffs": [
      {"file": "src/index.ts", "additions": 10, "deletions": 2}
    ]
  }
}
```

**Поля:**
- `role` — `"user"` (пользователь) или `"assistant"` (ответ AI)
- `summary.diffs` — краткая сводка изменений (только для сжатых сессий)

## 4. Таблица part

| Колонка | Тип | Описание |
|----------|-----|----------|
| `id` | TEXT PK | Уникальный ID (`prt_...`). |
| `message_id` | TEXT FK NOT NULL | Связь с message. |
| `session_id` | TEXT FK NOT NULL | Прямая связь с session (для быстрых запросов). |
| `time_created` | INTEGER NOT NULL | Timestamp (ms). |
| `time_updated` | INTEGER NOT NULL | Timestamp обновления. |
| `data` | TEXT (JSON) | Содержимое части. |

### 4.1 Типы parts

| Тип | Назначение | Ключевые поля data | Занимает место |
|-----|-----------|-------------------|----------------|
| `text` | Текстовый ответ модели | `text`, `type:"text"` | Умеренно |
| `tool` | Вызов инструмента | `tool`, `state.input`, `state.output`, `state.status` | Много (output) |
| `reasoning` | Chain of Thought | `text`, `time.start`, `time.end` | **Очень много (~77%)** |
| `patch` | Изменение файла | `filePath`, `state.input.content` | Много |
| `step-start` | Начало шага | `type:"step-start"` | Минимум |
| `step-finish` | Конец шага | `tokens`, `cost`, `reason` | Минимум |
| `compaction` | Сжатая сессия | `type:"compaction"` | Минимум |
| `file` | Файл как контекст | `filePath`, `content` | Много |
| `agent` | Агентское сообщение | — | Минимум |
| `retry` | Повторная попытка | — | Минимум |

### 4.2 Пример part с типом "reasoning"
```json
{
  "type": "reasoning",
  "text": "Let me analyze the problem step by step...\nFirst, I need to check the database...",
  "time": {
    "start": 1780294992826,
    "end": 1780294993738
  }
}
```

### 4.3 Пример part с типом "tool"
```json
{
  "type": "tool",
  "tool": "bash",
  "callID": "call_00_...",
  "state": {
    "status": "completed",
    "input": {
      "description": "Check git status",
      "command": "git status"
    },
    "output": "On branch main\nYour branch is up to date..."
  },
  "metadata": {
    "exit": 0,
    "truncated": false
  },
  "time": {
    "start": 1780295005675,
    "end": 1780295005829
  }
}
```

### 4.4 Пример part с типом "patch"
```json
{
  "type": "patch",
  "tool": "edit",
  "filePath": "src/index.ts",
  "state": {
    "status": "completed",
    "input": {
      "filePath": "src/index.ts",
      "oldString": "const x = 1;\n",
      "newString": "const x = 2;\n"
    }
  }
}
```

## 5. Таблица project

| Колонка | Тип | Описание |
|----------|-----|----------|
| `id` | TEXT PK | SHA1 хеш от git-корня. `'global'` для legacy/без проекта. |
| `worktree` | TEXT NOT NULL | Абсолютный путь к корню git-репозитория. |
| `vcs` | TEXT? | Тип VCS (`'git'` или NULL). |
| `name` | TEXT? | Название проекта (может быть NULL). |
| `icon_url` | TEXT? | Base64-иконка проекта. |
| `time_created` | INTEGER NOT NULL | Когда проект впервые обнаружен. |
| `time_updated` | INTEGER NOT NULL | Последнее обновление. |

### 5.1 Алгоритм вычисления project_id

```
1. opencode запускается в директории Q:\...\TestQA
2. git rev-parse --show-toplevel → Q:/User_Data/Desktop/TestQA
3. SHA1(forward-slash lowercased path) → a8a2d42272aeac95b2502345313a1f1866da532a
4. Сохраняется в project: {id: хеш, worktree: путь}
```

**Важно:** В разных версиях opencode алгоритм может отличаться.
Если сессия создана в одной версии, а открывается в другой — может не совпасть project_id.

## 6. Таблица `todo`

| Колонка | Тип | Описание |
|----------|-----|----------|
| `id` | TEXT PK | Уникальный ID. |
| `session_id` | TEXT FK | Связь с session. |
| `content` | TEXT | Текст задачи. |
| `status` | TEXT | `pending`, `completed`, `cancelled`. |
| `priority` | TEXT | `high`, `medium`, `low`. |
| `time_created` | INTEGER | Timestamp. |

## 7. Session_diff (внешние файлы)

**Расположение:** `~/.local/share/opencode/storage/session_diff/<session_id>.json`

**Назначение:** Хранит содержимое файлов, изменённых в сессии, для возможности отката (undo/redo).

**Размер:** Может достигать 40+ МБ на сессию.

**Структура:** JSON с массивом снимков файлов.

**Важно:** Diff-файлы не удаляются при очистке reasoning или сообщений.
Удаляются только при удалении самой сессии.
