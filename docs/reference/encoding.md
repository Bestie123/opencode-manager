# Encoding Repair Reference

> Doc-ID: REF-ENCODING-1 | Дата: 2026-06-21 | Связанные: [FIX-ENCODING-1], [MCP-OCDB-2], [GUIDE-TROUBLE-1]

## 1. Типы mojibake

В базах opencode встречаются два типа битой кириллицы:

| Тип | Симптом | Причина | Фикс |
|-----|---------|---------|------|
| **Latin1** | `Ð—Ð°Ð´Ð°ÑÐ¸` | UTF-8 байты прочитаны как Latin1 (Windows-1252) | `.encode("latin1").decode("utf-8")` |
| **CP866** | `╨Ч╨░╨┤╨░╤З╨╕` | UTF-8 байты прочитаны как CP866 (DOS Cyrillic) | CP866 lookup-table (см. ниже) |

Оба типа — результат **двойного кодирования**: исходные UTF-8 байты были интерпретированы как другая однобайтовая кодировка и перекодированы в UTF-8.

## 2. Принцип работы (CP866 mojibake)

### 2.1 Как возникает

Русский текст кодируется в UTF-8. Каждый символ кириллицы занимает 2 байта:

```
А (U+0410) → UTF-8: [0xD0, 0x90]
```

Если эти байты прочитать как CP866 (DOS Cyrillic), каждый байт превращается в символ:

```
0xD0 (208) → в CP866 это ╨ (U+2568)
0x90 (144) → в CP866 это Р (U+0420)
```

Результат: `╨Р` вместо `А`.

### 2.2 Как исправить

Процесс восстановления:

1. Взять каждый Unicode-символ mojibake-строки
2. Найти его CP866-байт через lookup-таблицу
3. Собрать последовательность байт
4. Декодировать как UTF-8

```
А = ╨ (U+2568) → CP866 байт 0xD0
А = Р (U+0420) → CP866 байт 0x90
→ [0xD0, 0x90] → UTF-8 decode → "А"
```

### 2.3 Почему не подходит latin1

Latin1-декодинг (`Buffer.from(text, "latin1").toString("utf-8")`) работает **только** если исходные байты при перекодировке не менялись. В случае CP866 байты **меняются**, потому что каждый байт превращается в ДРУГОЙ Unicode-символ (CP866 имеет свою кодировку, отличную от Latin1).

## 3. CP866 Lookup-таблица

```python
CP866_MAP = {
    # Заглавные А-Я (0x80-0x9F)
    0x0410: 0x80, 0x0411: 0x81, 0x0412: 0x82, 0x0413: 0x83, 0x0414: 0x84,
    0x0415: 0x85, 0x0416: 0x86, 0x0417: 0x87, 0x0418: 0x88, 0x0419: 0x89,
    0x041A: 0x8A, 0x041B: 0x8B, 0x041C: 0x8C, 0x041D: 0x8D, 0x041E: 0x8E,
    0x041F: 0x8F, 0x0420: 0x90, 0x0421: 0x91, 0x0422: 0x92, 0x0423: 0x93,
    0x0424: 0x94, 0x0425: 0x95, 0x0426: 0x96, 0x0427: 0x97, 0x0428: 0x98,
    0x0429: 0x99, 0x042A: 0x9A, 0x042B: 0x9B, 0x042C: 0x9C, 0x042D: 0x9D,
    0x042E: 0x9E, 0x042F: 0x9F,
    # Строчные а-п (0xA0-0xAF)
    0x0430: 0xA0, 0x0431: 0xA1, 0x0432: 0xA2, 0x0433: 0xA3, 0x0434: 0xA4,
    0x0435: 0xA5, 0x0436: 0xA6, 0x0437: 0xA7, 0x0438: 0xA8, 0x0439: 0xA9,
    0x043A: 0xAA, 0x043B: 0xAB, 0x043C: 0xAC, 0x043D: 0xAD, 0x043E: 0xAE,
    0x043F: 0xAF,
    # Псевдографика (0xB0-0xDF)
    0x2591: 0xB0, 0x2592: 0xB1, 0x2593: 0xB2, 0x2502: 0xB3,
    0x2524: 0xB4, 0x2561: 0xB5, 0x2562: 0xB6, 0x2556: 0xB7,
    0x2555: 0xB8, 0x2563: 0xB9, 0x2551: 0xBA, 0x2557: 0xBB,
    0x255D: 0xBC, 0x255C: 0xBD, 0x255B: 0xBE, 0x2510: 0xBF,
    0x2514: 0xC0, 0x2534: 0xC1, 0x252C: 0xC2, 0x251C: 0xC3,
    0x2500: 0xC4, 0x253C: 0xC5, 0x255E: 0xC6, 0x255F: 0xC7,
    0x255A: 0xC8, 0x2554: 0xC9, 0x2569: 0xCA, 0x2566: 0xCB,
    0x2560: 0xCC, 0x2550: 0xCD, 0x256C: 0xCE, 0x2567: 0xCF,
    0x2568: 0xD0, 0x2564: 0xD1, 0x2565: 0xD2, 0x2559: 0xD3,
    0x2558: 0xD4, 0x2552: 0xD5, 0x2553: 0xD6, 0x256B: 0xD7,
    0x256A: 0xD8, 0x2518: 0xD9, 0x250C: 0xDA, 0x2588: 0xDB,
    0x2584: 0xDC, 0x258C: 0xDD, 0x2590: 0xDE, 0x2580: 0xDF,
    # Строчные Ѐ-Џ (0xE0-0xEF)
    0x0440: 0xE0, 0x0441: 0xE1, 0x0442: 0xE2, 0x0443: 0xE3,
    0x0444: 0xE4, 0x0445: 0xE5, 0x0446: 0xE6, 0x0447: 0xE7,
    0x0448: 0xE8, 0x0449: 0xE9, 0x044A: 0xEA, 0x044B: 0xEB,
    0x044C: 0xEC, 0x044D: 0xED, 0x044E: 0xEE, 0x044F: 0xEF,
    # Прочее (0xF0-0xFF)
    0x0401: 0xF0, 0x0451: 0xF1, 0x0404: 0xF2, 0x0454: 0xF3,
    0x0407: 0xF4, 0x0457: 0xF5, 0x040E: 0xF6, 0x045E: 0xF7,
    0x00B0: 0xF8, 0x2219: 0xF9, 0x00B7: 0xFA, 0x221A: 0xFB,
    0x207F: 0xFC, 0x00B2: 0xFD, 0x25A0: 0xFE, 0x00A0: 0xFF,
}
```

## 4. Код для восстановления

### 4.1 Python

```python
import sqlite3
from pathlib import Path

CP866_MAP = { ... }  # таблица из секции 3

def decode_mojibake(text: str) -> str:
    if not text:
        return text
    buf = bytearray()
    for ch in text:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            buf.append(cp)
        elif cp in CP866_MAP:
            buf.append(CP866_MAP[cp])
        else:
            buf.append(0x3F)
    return buf.decode("utf-8", errors="replace")


def is_mojibake(text: str) -> bool:
    if not text:
        return False
    has_box = any(0x2500 <= ord(c) <= 0x257F for c in text)
    has_cyr = any(ord(c) in CP866_MAP for c in text)
    if not (has_box and has_cyr):
        return False
    decoded = decode_mojibake(text)
    return decoded != text and any(0x0400 <= ord(c) <= 0x04FF for c in decoded)


def fix_database(db_path: str, dry_run: bool = True) -> list[dict]:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    rows = conn.execute("SELECT id, title FROM session").fetchall()
    affected = []

    for sid, title in rows:
        if not title or not is_mojibake(title):
            continue
        decoded = decode_mojibake(title)
        affected.append({"id": sid, "original": title[:100], "decoded": decoded[:100]})
        if not dry_run:
            conn.execute("UPDATE session SET title = ? WHERE id = ?", (decoded, sid))

    if not dry_run:
        conn.commit()
    conn.close()
    return affected


# Использование
preview = fix_database("opencode.db", dry_run=True)     # предпросмотр
result = fix_database("opencode.db", dry_run=False)     # реальный фикс
```

### 4.2 TypeScript (Bun)

```typescript
import { Database } from "bun:sqlite";

const CP866_MAP: Record<number, number> = { ... };  // таблица из секции 3

function decodeMojibake(text: string): string {
  const bytes: number[] = [];
  for (const ch of text) {
    const cp = ch.codePointAt(0)!;
    if (cp >= 0x20 && cp <= 0x7E) {
      bytes.push(cp);
    } else if (CP866_MAP[cp] !== undefined) {
      bytes.push(CP866_MAP[cp]);
    } else {
      bytes.push(0x3F);
    }
  }
  return Buffer.from(bytes).toString("utf-8");
}

function isMojibake(text: string): boolean {
  if (!text) return false;
  const hasBox = [...text].some(c => {
    const cp = c.codePointAt(0)!;
    return cp >= 0x2500 && cp <= 0x257F;
  });
  const hasCyr = [...text].some(c => CP866_MAP[c.codePointAt(0)!] !== undefined);
  if (!(hasBox && hasCyr)) return false;
  const decoded = decodeMojibake(text);
  return decoded !== text && [...decoded].some(c => {
    const cp = c.codePointAt(0)!;
    return cp >= 0x0400 && cp <= 0x04FF;
  });
}

// Использование
const db = new Database("opencode.db");
const rows = db.query("SELECT id, title FROM session").all() as any[];
for (const row of rows) {
  if (!isMojibake(row.title)) continue;
  const decoded = decodeMojibake(row.title);
  db.query("UPDATE session SET title = ? WHERE id = ?").run(decoded, row.id);
  console.log(`Fixed: ${row.id}`);
}
db.close();
```

### 4.3 Через MCP (рекомендуемый способ)

Запустите в opencode-сессии:

```json
// 1. Детекция
oc_detect_mojibake({ "db": "opencode.db" })

// 2. Предпросмотр
oc_fix_mojibake({ "db": "opencode.db", "dry_run": true })

// 3. Исправление (создаст backup автоматически)
oc_fix_mojibake({ "db": "opencode.db", "confirm": true })
```

## 5. Диагностика

### 5.1 SQL-детекция

```sql
-- Поиск CP866 mojibake
SELECT id, title FROM session WHERE title LIKE '%╨%' OR title LIKE '%╤%' OR title LIKE '%╥%';

-- Поиск Latin1 mojibake
SELECT id, title FROM session WHERE title LIKE '%Ð%' OR title LIKE '%Ñ%';
```

### 5.2 Визуальная детекция

CP866 mojibake содержит символы псевдографики (Box Drawing, U+2500+):
```
╨ ╤ ╥ ╗ ║ ╔ ╚ ╝ ═ ╬ ╩ ╦ ╠ ╣
```

Latin1 mojibake содержит символы:
```
Ð Ñ Ò Ó Ô Õ Ö × Ø Ù Ú Û Ü Ý Þ ß
```

### 5.3 Автоматическая детекция (MCP)

```
oc_detect_mojibake({ "db": "opencode.db" })
→ total_affected: 48, affected_sessions: 48
```

## 6. Типичные примеры

| Mojibake (CP866) | Оригинал (русский) |
|---|---|
| `╨Р╤Г╨┤╨╕╤В docs: ╤Б╤В╤А╤Г╨║╤В╤Г╤А╨░` | `Аудит docs: структура` |
| `╨Я╨╛╨╕╤Б╨║ ╨▒╨╡╤Б╨┐╨╗╨░╤В╨╜╤Л╤Е ╨╝╨╛╨┤╨╡╨╗╨╡╨╣` | `Поиск бесплатных моделей` |
| `╨Ф╨╛╨▒╨░╨▓╨╗╨╡╨╜╨╕╨╡ ╤Б╤В╨░╤В╤Г╤Б╨╛╨▓` | `Добавление статусов` |
| `╨Ш╤Б╨┐╤А╨░╨▓╨╗╨╡╨╜╨╕╨╡ ╨┐╨╡╤А╨╡╨║╨╗╤О╤З╨╡╨╜╨╕╤П` | `Исправление переключения` |
| `╨Я╤А╨╕╨▓╨╡╤В╤Б╤В╨▓╨╕╨╡` | `Приветствие` |

## 7. Предотвращение

1. **Always use UTF-8** for SQLite databases — set `PRAGMA encoding = "UTF-8"` 
2. **Avoid opening .db files** in Notepad/Notepad++ with auto-detect encoding
3. **When exporting JSON**, ensure proper UTF-8 without BOM:
   ```python
   json.dumps(data, ensure_ascii=False) + "\n"
   ```
4. **When importing external data**, explicitly convert to UTF-8:
   ```python
   text.encode("utf-8").decode("utf-8")
   ```
5. **Run health checks regularly** via MCP:
   ```
   oc_detect_mojibake({ "db": "opencode.db" })
   ```
