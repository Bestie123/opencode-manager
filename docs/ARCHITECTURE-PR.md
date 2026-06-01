# OpenCode Architecture

> OpenCode — AI coding agent for the terminal. This document describes its core architecture: data layer, project resolution, session lifecycle, and the Desktop/CLI split.

> **Source:** `docs/ARCHITECTURE.md` (proposed)

---

## 1. Project structure

```
packages/
├── core/              # Shared core: database, project resolution, git, filesystem
│   └── src/
│       ├── database/  # SQLite connection, migrations, path resolution
│       ├── project.ts # ProjectV2: resolve (ID from directory/git remote)
│       └── session/   # SessionTable schema, projector (event → SQL)
│
├── opencode/          # CLI, TUI, HTTP server, Desktop integration
│   └── src/
│       ├── cli/       # CLI commands (session list, db path, server)
│       ├── project/   # fromDirectory — resolve + upsert + migrate sessions
│       ├── session/   # listByProject, listGlobal, Service
│       └── server/    # HTTP API, sidecar
│
└── sdk/               # TypeScript SDK for API consumers
```

## 2. Data layer

### 2.1 Databases

OpenCode stores data in SQLite. The database file is determined by the **installation channel**:

```typescript
// packages/core/src/database/database.ts
function path() {
  // 1. Env variable overrides everything
  if (Flag.OPENCODE_DB) return resolve(Flag.OPENCODE_DB)

  // 2. Stable channels → opencode.db
  if (["latest", "beta", "prod"].includes(InstallationChannel)
      || env.OPENCODE_DISABLE_CHANNEL_DB)
    return join(Global.Path.data, "opencode.db")

  // 3. Everything else → opencode-{channel}.db
  return join(Global.Path.data, `opencode-${InstallationChannel}.db`)
}
```

| Channel | DB file | Use case |
|---------|---------|----------|
| `latest` | `opencode.db` | Stable Desktop app (packaged) |
| `beta` | `opencode.db` | Beta Desktop app |
| `prod` | `opencode.db` | Production CLI builds |
| `dev` | `opencode-dev.db` | Development CLI (npm global) |
| `local` | `opencode-local.db` | Running from source |
| any other | `opencode-{channel}.db` | Custom builds |

The channel is set at build time through the `OPENCODE_CHANNEL` constant.

**Important:** Desktop app and CLI can use different databases. Always check with `opencode db path`.

### 2.2 Schema (key tables)

#### `project`

```sql
CREATE TABLE project (
    id TEXT PRIMARY KEY,           -- 'global' or a content-hash
    worktree TEXT,                 -- '/' for global, git root for projects
    vcs TEXT,                      -- 'git' or NULL
    name TEXT,
    icon_url TEXT,
    icon_color TEXT,
    time_created INTEGER,          -- unix timestamp in ms
    time_updated INTEGER,
    time_initialized INTEGER,
    sandboxes TEXT,                -- JSON array of additional worktrees
    commands TEXT,
    icon_url_override TEXT
);
```

Two kinds of projects:

| `id` | `worktree` | `vcs` | Meaning |
|------|------------|-------|---------|
| `global` | `/` | `NULL` | Fallback project (no git repo or unresolvable) |
| `<hash>` | `Q:\...\TestQA` | `git` | A specific git repository |

**Constraint:** `global.id='global'` must always have `worktree='/'` and `vcs=NULL`. These values are enforced in `fromDirectory()` (see §3.2).

#### `session`

```sql
CREATE TABLE session (
    id TEXT PRIMARY KEY,
    project_id TEXT,               -- FK to project.id
    parent_id TEXT,                -- NULL for root sessions, set for subagents
    slug TEXT,
    directory TEXT,                -- Windows: backslashes; Unix: forward slashes
    path TEXT,                     -- Always forward slashes
    title TEXT,
    version TEXT,
    model TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    tokens_input INTEGER,
    tokens_output INTEGER,
    tokens_reasoning INTEGER,
    tokens_cache_read INTEGER,
    tokens_cache_write INTEGER,
    cost REAL,
    -- + summary columns, metadata, etc.
);

CREATE INDEX session_project_idx ON session(project_id);
CREATE INDEX session_parent_idx ON session(parent_id);
```

Key rules:
- `directory` uses platform-native separators (`\` on Windows, `/` on Unix)
- `path` always uses `/` (forward slashes) — used for `LIKE` pattern matching
- `project_id` must match a row in `project` (or `'global'`)
- `parent_id IS NULL` → root session; `parent_id IS NOT NULL` → subagent session

#### `message` and `part`

```sql
CREATE TABLE message (
    id TEXT PRIMARY KEY,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                      -- JSON: { role, summary, diffs, ... }
);

CREATE TABLE part (
    id TEXT PRIMARY KEY,
    message_id TEXT,
    session_id TEXT,
    time_created INTEGER,
    time_updated INTEGER,
    data TEXT                      -- JSON: { type, text, state, tool, ... }
);
```

Part types (from `data.type`):
- `text` — main response content
- `tool` — tool call (with `state.status: completed|error`)
- `reasoning` — chain-of-thought tokens
- `patch` — file edit (with `filePath`)
- `step-start` / `step-finish` — action boundaries
- `compaction` — compacted/rolled-up content
- `file` — file content

## 3. Project resolution

The project resolution chain determines which project a session belongs to based on the directory it was created in.

### 3.1 `ProjectV2.resolve(directory)` — ID resolution

**File:** `packages/core/src/project.ts`

```typescript
resolve(input: AbsolutePath) → {
  previous?: ID       // previous project ID from cache
  id: ID              // resolved project ID
  directory: string   // git worktree root (or volume root for non-git)
  vcs?: Vcs           // { type: 'git', store: path } or undefined
}
```

Algorithm:

```
1. git.find(input)
   └─ Success → repo found
   │  ├─ id = remote(repo) ?? cached(repo) ?? root(repo) ?? 'global'
   │  │    remote(repo) — hash of git remote URL (e.g., "github.com/user/repo")
   │  │    cached(repo) — from .git/opencode file (persisted after first resolve)
   │  │    root(repo)   — hash of git worktree path
   │  └─ vcs = { type: 'git', store: repo.store }
   │
   └─ Failure → no git repo
      ├─ id = 'global'
      └─ vcs = undefined
```

### 3.2 `fromDirectory(directory)` — full project lifecycle

**File:** `packages/opencode/src/project/project.ts`

```
fromDirectory(directory)
│
├─ 1. data = ProjectV2.resolve(directory)
│
├─ 2. worktree = data.id === 'global' && !data.vcs ? "/" : data.directory
│     // Global project → worktree = "/"
│     // Named project → worktree = git root
│
├─ 3. projectID = ID.make(data.id)
│
├─ 4. migrateProjectId(data.previous, projectID)
│     // Migrate sessions from old project ID to new one
│     // Skips if oldID === 'global' or oldID === newID
│
├─ 5. Upsert project row
│     INSERT INTO project (id, worktree, vcs, ...)
│     ON CONFLICT(id) DO UPDATE SET worktree = ..., vcs = ...
│
├─ 6. Migrate orphaned global sessions (only for non-global projects)
│     UPDATE session SET project_id = projectID
│     WHERE project_id = 'global' AND directory = data.directory
│
└─ 7. Return { project: Info, sandbox: string }
```

**Critical rule:** When the current directory has no git repo, `fromDirectory` sets the global project back to its canonical state (`worktree=/`, `vcs=NULL`). This self-healing prevents permanent corruption.

## 4. Session lifecycle

### 4.1 Listing sessions

**TUI session dialog** loads via `bootstrap()` → `sync.tsx`:

```typescript
listSessions() {
  const result = await sdk.client.session.list({
    start: Date.now() - 30 * 24 * 60 * 60 * 1000,  // last 30 days
    scope: "project",   // filter by current project
    roots: true,        // only root sessions (no subagents)
  })
}
```

The underlying SQL:

```typescript
function listByProject(db, input) {
  const conditions = [
    eq(SessionTable.project_id, input.projectID),  // ALWAYS filtered by project
    isNull(SessionTable.parent_id),                  // roots only
    gt(SessionTable.time_updated, input.start),     // 30-day window
  ]
  if (input.directory) conditions.push(eq(...))
  if (input.path)      conditions.push(like(...))
  return db.select().from(SessionTable)
    .where(and(...conditions))
    .orderBy(desc(SessionTable.time_updated))
    .limit(input.limit ?? 100)
}
```

**Global (experimental) listing:**

`GET /experimental/session` returns sessions from ALL projects, joined with project info.

### 4.2 Creating a session

```
1. User sends a message
2. Session projector (packages/core/src/session/projector.ts) handles the event
3. INSERT INTO session (id, project_id, directory, ...)
4. Project_id = current context's project.id (from fromDirectory)
```

### 4.3 Moving a session between projects

The `fromDirectory` function handles automatic migration when a project is re-identified:

```typescript
migrateProjectId(oldID, newID) {
  UPDATE session SET project_id = newID WHERE project_id = oldID
  UPDATE workspace SET project_id = newID WHERE project_id = oldID
  // Migrate permissions, delete old project row
}
```

This is triggered when:
- Git remote changes (e.g., repo is cloned to a new URL)
- Git worktree changes (e.g., parent directory is renamed)
- `.git/opencode` is manually edited

### 4.4 Session diff files

Outside SQLite, OpenCode stores incremental diffs:

```
~/.local/share/opencode/storage/session_diff/
└── {session_id}.json    — accumulated diff data
```

These are separate from the SQLite store and are used for workspace/project-level caching.

## 5. Desktop application (Tauri)

### 5.1 Architecture

```
┌─────────────────────┐     HTTP API      ┌─────────────────────┐
│  OpenCode Desktop   │ ◄──────────────► │  OpenCode sidecar    │
│  (Tauri + WebView)  │  localhost:XXXXX  │  (OpenCode server)   │
└─────────────────────┘                   └──────────┬──────────┘
                                                     │
                                                     ▼
                                            ┌─────────────────────┐
                                            │  SQLite database     │
                                            │  (opencode.db)       │
                                            └─────────────────────┘
```

- The Desktop app is a Tauri v2 application (Rust backend + WebView frontend)
- On startup, it spawns itself as a sidecar: `OpenCode.exe server --port XXXXX`
- The sidecar runs the same `packages/opencode` server code used by the CLI
- Communication is via HTTP on `localhost`

### 5.2 State store (Tauri plugin-store)

**Location:** `%APPDATA%/ai.opencode.desktop/`

| File | Purpose | Format |
|------|---------|--------|
| `opencode.global.dat` | Global app state (sidebar, projects, settings) | JSON |
| `opencode.workspace.{encoded_path}.{suffix}.dat` | Per-directory workspace data | JSON |
| `default.dat` | UI preferences cache | JSON |
| `opencode.settings` | Migration flags | JSON |

**Important:** Tauri Store expects **clean UTF-8 without BOM**. Files written with BOM (`EF BB BF`) will fail to parse:

```
renderer.log: Uncaught (in promise) Error: Error invoking remote method 'store-set':
SyntaxError: Unexpected token 'ï»¿', "ï»¿{"prompt-"... is not valid JSON
```

### 5.3 Logs

```
%APPDATA%/ai.opencode.desktop/logs/{timestamp}/
├── main.log       — main process (startup, sidecar, lifecycle)
├── server.log     — sidecar server process
├── renderer.log   — WebView renderer (UI errors, store errors)
├── crash.log      — crash reporter
├── network.log    — HTTP communication
└── pty.log        — terminal emulator
```

## 6. Key architectural decisions

| Decision | Rationale |
|----------|-----------|
| Channel-based DB isolation | Dev and stable builds don't interfere; reduces risk for nightly users |
| `project_id` on every session | Enables project-scoped session listing without JOINs |
| `parent_id` for subagents | Clean tree structure; TUI always filters `IS NULL` for root sessions |
| Global project is a real row | Simplifies queries: every session has a non-null `project_id` |
| No cascade delete on project | Prevents accidental data loss; sessions survive project re-identification |
| Sidecar architecture (Desktop) | Reuses all CLI server code; single API surface for both Desktop and CLI |
| Tauri Store for UI state | Separate from SQLite; allows reactive frontend patterns |

## 7. Useful queries

```sql
-- All sessions with project info
SELECT s.id, s.title, p.id as project, p.worktree
FROM session s
LEFT JOIN project p ON p.id = s.project_id
ORDER BY s.time_updated DESC;

-- Session count per project
SELECT project_id, COUNT(*) as cnt
FROM session
GROUP BY project_id;

-- Orphaned sessions (project missing)
SELECT s.id, s.project_id
FROM session s
LEFT JOIN project p ON p.id = s.project_id
WHERE p.id IS NULL;

-- Sessions with path/directory inconsistency
SELECT id, directory, path
FROM session
WHERE (directory LIKE '%/%' AND path IS NULL)
   OR (directory NOT LIKE '%/%' AND path IS NOT NULL
       AND replace(directory, '\', '/') != path);

-- Recent root sessions (last 30 days)
SELECT id, title, project_id, directory
FROM session
WHERE parent_id IS NULL
  AND time_updated > (strftime('%s','now') - 30*86400) * 1000
ORDER BY time_updated DESC;
```

## 8. Glossary

| Term | Definition |
|------|------------|
| Session | A single conversation with the AI agent |
| Project | A git repository (or the `global` virtual project) |
| Worktree | Git worktree root path |
| Subagent | Child session with `parent_id`, created by `@explore`/`@general` |
| Sidecar | Background `opencode server` process providing HTTP API |
| Channel | Build variant (`dev`, `latest`, `beta`, `prod`) determining DB filename |
| Tauri Store | Tauri's plugin-store: JSON key-value files for UI state |
| BOM | Byte Order Mark (`EF BB BF`); breaks Tauri Store if present |
| Projector | Event-sourcing mechanism that writes session events to SQLite |
