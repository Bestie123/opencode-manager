# SQL-запросы для диагностики OpenCode

> Doc-ID: REF-SQL-1 | Дата: 2026-06-02 | Связанные: [ARCH-v2], [MCP-OCDB-1], [GUIDE-TROUBLE-1]

## 1. Базовые запросы

### 1.1 Сколько сессий в каждой БД
```sql
SELECT 'opencode.db' AS db, COUNT(*) AS sessions FROM session
UNION ALL
SELECT 'opencode-dev.db', COUNT(*) FROM main.session;
```

### 1.2 Сессии по проектам
```sql
SELECT p.worktree, COUNT(s.id) AS sessions
FROM session s
JOIN project p ON s.project_id = p.id
GROUP BY p.id
ORDER BY sessions DESC;
```

### 1.3 Сессии с project_id='global' (без проекта)
```sql
SELECT id, title, time_created, time_archived
FROM session
WHERE project_id = 'global' AND parent_id IS NULL
ORDER BY time_created DESC;
```

### 1.4 Subagent-сессии
```sql
SELECT id, parent_id, title
FROM session
WHERE parent_id IS NOT NULL
ORDER BY parent_id;
```

### 1.5 Архивные сессии
```sql
SELECT id, title, time_archived
FROM session
WHERE time_archived IS NOT NULL
ORDER BY time_archived DESC;
```

### 1.6 Самые тяжёлые сессии
```sql
SELECT s.id, s.title,
       ROUND(SUM(LENGTH(p.data)) / 1048576.0, 1) AS size_mb,
       COUNT(DISTINCT m.id) AS messages
FROM session s
JOIN part p ON p.session_id = s.id
JOIN message m ON m.session_id = s.id
GROUP BY s.id
ORDER BY size_mb DESC
LIMIT 20;
```

## 2. Диагностика проблем

### 2.1 Orphan-дети (родитель удалён)
```sql
SELECT s1.id AS child_id, s1.parent_id, s1.title
FROM session s1
WHERE s1.parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id);
```

### 2.2 Сессии с некорректным directory
```sql
SELECT id, directory, path
FROM session
WHERE directory LIKE '%/%'
   OR path IS NULL
   OR path = ''
   OR directory != REPLACE(path, '/', '\\');
```

### 2.3 Дубли project_id (один worktree — разные хеши)
```sql
SELECT worktree, COUNT(*) AS cnt, GROUP_CONCAT(id, ', ') AS project_ids
FROM project
WHERE id != 'global'
GROUP BY worktree
HAVING COUNT(*) > 1;
```

### 2.4 Сессии, не привязанные ни к одному существующему проекту
```sql
SELECT s.id, s.title, s.project_id
FROM session s
WHERE s.project_id != 'global'
  AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id = s.project_id);
```

### 2.5 Поиск сессии по части названия
```sql
SELECT id, title, project_id, directory
FROM session
WHERE title LIKE '%aws%' OR title LIKE '%toolkit%'
ORDER BY time_created DESC;
```

## 3. Сравнение БД

### 3.1 Сессии, которые есть в opencode.db, но нет в opencode-dev.db
Используй MCP: `oc_check_orphans`

Либо программно:
```bash
# Сравнить через Python:
# only_in_opencode_db = set(opencode_ids) - set(dev_ids)
```

### 3.2 Разные project_id для одной сессии в разных БД
```sql
-- Выполнить в каждой БД, сравнить:
SELECT id, project_id FROM session WHERE id = 'ses_17e2b66d4ffeGa1wwrFEn0aKNN';
```

## 4. Очистка

### 4.1 Разархивировать все сессии
```sql
UPDATE session SET time_archived = NULL WHERE time_archived IS NOT NULL;
```

### 4.2 Удалить orphan-детей
```sql
DELETE FROM part WHERE session_id IN (
  SELECT s1.id FROM session s1
  WHERE s1.parent_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)
);
DELETE FROM message WHERE session_id IN (
  SELECT s1.id FROM session s1
  WHERE s1.parent_id IS NOT NULL
    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)
);
DELETE FROM session WHERE parent_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = session.parent_id);
```

### 4.3 Обновить project_id для legacy-сессий
```sql
UPDATE session SET project_id = 'a8a2d42272aeac95b2502345313a1f1866da532a'
WHERE project_id = 'global'
  AND id IN (SELECT id FROM session
             WHERE directory LIKE '%TestQA%');
```

### 4.4 Сбросить project_id='global' для сессий без проекта
```sql
UPDATE session SET project_id = 'global'
WHERE project_id NOT IN (SELECT id FROM project WHERE id != 'global');
```

## 5. Статистика

### 5.1 Размер БД по таблицам (приблизительно)
```sql
SELECT 'session' AS tbl, COUNT(*) AS rows, ROUND(SUM(LENGTH(id) + LENGTH(title) + LENGTH(directory) + LENGTH(model)) / 1048576.0, 1) AS size_mb FROM session
UNION ALL
SELECT 'message', COUNT(*), ROUND(SUM(LENGTH(data)) / 1048576.0, 1) FROM message
UNION ALL
SELECT 'part', COUNT(*), ROUND(SUM(LENGTH(data)) / 1048576.0, 1) FROM part;
```

### 5.2 Части по типам
```sql
SELECT json_extract(data, '$.type') AS ptype,
       COUNT(*) AS cnt,
       ROUND(SUM(LENGTH(data)) / 1048576.0, 1) AS size_mb
FROM part
GROUP BY ptype
ORDER BY size_mb DESC;
```

### 5.3 Процент reasoning от общего размера
```sql
SELECT
  ROUND(SUM(CASE WHEN json_extract(data, '$.type') = 'reasoning' THEN LENGTH(data) ELSE 0 END) * 100.0 / SUM(LENGTH(data)), 1) AS reasoning_pct
FROM part;
```

### 5.4 Сессии за последние 30 дней
```sql
SELECT COUNT(*) AS recent_sessions
FROM session
WHERE parent_id IS NULL
  AND time_created > (strftime('%s', 'now') - 30*86400) * 1000;
```

### 5.5 Среднее количество сообщений на сессию
```sql
SELECT AVG(msg_cnt) AS avg_messages_per_session
FROM (
  SELECT session_id, COUNT(*) AS msg_cnt
  FROM message
  GROUP BY session_id
);
```

## 6. Проекты

### 6.1 Все известные проекты
```sql
SELECT id, worktree, vcs, time_created FROM project ORDER BY time_created DESC;
```

### 6.2 Проекты с иконками (base64)
```sql
SELECT id, worktree,
       CASE WHEN LENGTH(icon_url) > 100 THEN 'base64 (large)' ELSE icon_url END AS icon
FROM project WHERE icon_url IS NOT NULL;
```

### 6.3 Проекты, созданные за последнюю неделю
```sql
SELECT id, worktree, time_created
FROM project
WHERE time_created > (strftime('%s', 'now') - 7*86400) * 1000;
```
