#!/usr/bin/env python3
"""MCP сервер для инспекции и управления SQLite-базами OpenCode.

Doc-ID: MCP-OCDB-2
Дата: 2026-06-05
Связанные: [ARCH-v2], [DB-SELECTOR-1], [MCP-OCDB-1], [TOOL-USAGE-1]

Протокол: MCP stdio (совместим с opencode MCP).
Инструменты: CRUD, анализ, сравнение БД, SQL.

v2: Добавлены CRUD-операции:
- oc_delete_messages / oc_delete_parts
- oc_update_session / oc_update_message
- oc_compact_session (умная очистка)
- oc_analyze_session (семантический анализ)
- oc_truncate_session
- oc_backup_db
"""

from pathlib import Path
from typing import Any
import json
import os
import sqlite3
import shutil
from datetime import datetime

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult


BASE = Path.home() / ".local" / "share" / "opencode"
server = Server("opencode-db-inspector")

# Safety: require confirmation for destructive ops
# Pass confirm=true in arguments to execute
DESTRUCTIVE_TOOLS = {
    "oc_delete_messages", "oc_delete_parts", "oc_truncate_session",
    "oc_compact_session", "oc_exec_sql", "oc_fix_mojibake",
}


def _list_db_files() -> list[dict[str, Any]]:
    """Scan all opencode*.db files in the data directory with health status."""
    dbs = []
    for f in sorted(BASE.glob("opencode*.db")):
        name = f.name
        if name.endswith(("-shm", "-wal")) or ".backup-" in name:
            continue
        info = {"path": str(f), "name": name, "size": f.stat().st_size}

        # Check WAL/SHM existence
        wal_exists = (BASE / (name + "-wal")).exists()
        shm_exists = (BASE / (name + "-shm")).exists()
        info["wal_exists"] = wal_exists
        info["shm_exists"] = shm_exists

        try:
            conn = sqlite3.connect(str(f), timeout=2)
            c = conn.cursor()
            info["sessions"] = c.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            info["openable"] = True
            info["status"] = "ok"

            # Quick integrity
            c.execute("PRAGMA integrity_check")
            ic = c.fetchone()[0]
            if ic != "ok":
                info["status"] = "malformed"
                info["integrity"] = ic

            conn.close()
        except sqlite3.DatabaseError as e:
            info["sessions"] = -1
            info["openable"] = False
            info["status"] = "malformed"
            info["error"] = str(e)
            if wal_exists or shm_exists:
                info["likely_cause"] = "WAL/SHM conflict: delete -wal and -shm files"
        except Exception as e:
            info["sessions"] = -1
            info["openable"] = False
            info["status"] = "error"
            info["error"] = str(e)

        dbs.append(info)
    return dbs


def _resolve_db(db: str) -> str:
    """Resolve short name to full path."""
    for d in _list_db_files():
        if db in (d["name"], d["path"], d["name"].replace(".db", "")):
            return d["path"]
    return str(BASE / db) if not db.startswith(str(BASE)) else db


def _connect(db_path: str) -> sqlite3.Connection:
    """Open connection with WAL mode for writes."""
    conn = sqlite3.connect(_resolve_db(db_path), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _backup_db(db_path: str) -> str:
    """Create timestamped backup to Q:/OPENCODEBACKUP."""
    resolved = _resolve_db(db_path)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_dir = Path("Q:/OPENCODEBACKUP")
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"opencode.backup-{ts}.db"
    shutil.copy2(resolved, backup_path)
    return str(backup_path)


def _check_confirm(arguments: dict) -> bool:
    """Check that confirm=true is passed for destructive operations."""
    return arguments.get("confirm", False) is True


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        # ── READ tools ──
        Tool(
            name="oc_list_dbs",
            description="List all opencode SQLite databases (path, sessions, size).",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="oc_list_sessions",
            description="List sessions with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name (default: opencode.db)"},
                    "project_id": {"type": "string"},
                    "parent_id": {"type": "string", "description": "'null'=roots, 'any'=children, or exact ID"},
                    "search": {"type": "string"},
                    "limit": {"type": "number", "default": 100},
                },
                "required": [],
            },
        ),
        Tool(
            name="oc_get_session",
            description="Get full session: metadata + messages + parts.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="oc_read_messages",
            description="Read messages from a session in clean text format. Returns role, time, and content for each message. Supports limiting to last N messages, offset-based ranges, or reading a single message by ID.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name (default: opencode.db)"},
                    "session_id": {"type": "string", "description": "Session ID (default: latest session)"},
                    "message_id": {"type": "string", "description": "Read a single message by ID (overrides limit/offset)"},
                    "limit": {"type": "number", "description": "How many messages. Without offset: last N messages. With offset: take N messages after skipping offset."},
                    "offset": {"type": "number", "description": "Skip first N messages (chronological). Use with limit for range, e.g. offset=36 limit=58 = messages 37-94."},
                    "include_parts": {"type": "boolean", "description": "Include raw parts in output (default: false)"},
                    "tool_type": {"type": "string", "description": "Filter by tool type (e.g. 'edit', 'read', 'bash'). Only parts matching this tool type are returned."},
                    "file_path": {"type": "string", "description": "Filter by file path (substring match). Only edit tools targeting this file are returned."},
                },
                "required": [],
            },
        ),
        Tool(
            name="oc_get_children",
            description="Get child sessions recursively.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "parent_id": {"type": "string"},
                    "recursive": {"type": "boolean", "default": True},
                },
                "required": ["parent_id"],
            },
        ),
        Tool(
            name="oc_check_orphans",
            description="Compare opencode.db vs opencode-dev.db for orphans.",
            inputSchema={"type": "object", "properties": {}, "required": []},
        ),
        Tool(
            name="oc_query",
            description="Run SELECT query against database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "sql": {"type": "string", "description": "SELECT query"},
                },
                "required": ["db", "sql"],
            },
        ),
        # ── ANALYSIS tools ──
        Tool(
            name="oc_health_check",
            description="Deep health check for ALL database files. Detects malformed DBs, WAL/SHM conflicts, schema mismatches, orphaned data. Returns detailed report with fix suggestions.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Specific DB to check (optional, checks all if omitted)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="oc_repair_wal",
            description="Remove stale WAL/SHM files that cause 'database disk image is malformed' errors. Creates backup first.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name to repair"},
                    "confirm": {"type": "boolean", "description": "Must be true to execute"},
                    "dry_run": {"type": "boolean", "description": "Show what would be deleted without doing it"},
                },
                "required": ["db", "confirm"],
            },
        ),
        Tool(
            name="oc_analyze_session",
            description="Semantic analysis: find redundant messages, large parts, stats.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string"},
                },
                "required": ["session_id"],
            },
        ),
        # ── BACKUP ──
        Tool(
            name="oc_backup_db",
            description="Create timestamped backup of database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database to backup"},
                },
                "required": [],
            },
        ),
        # ── ENCODING tools ──
        Tool(
            name="oc_detect_mojibake",
            description="Scan session titles for CP866 mojibake (corrupted Cyrillic encoding). Returns affected sessions with decoded previews.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name (default: opencode.db)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="oc_fix_mojibake",
            description="Fix CP866 mojibake in session titles. Uses CP866→UTF-8 reverse mapping. REQUIRES confirm=true. Creates backup.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name (default: opencode.db)"},
                    "confirm": {"type": "boolean", "description": "Must be true to execute"},
                    "dry_run": {"type": "boolean", "description": "Show what would be fixed without actually fixing"},
                    "table": {"type": "string", "description": "Also fix in message/part table? Options: session (default), message, part, all"},
                },
                "required": [],
            },
        ),
        # ── UPDATE tools ──
        Tool(
            name="oc_update_session",
            description="Update session metadata fields (title, directory, project_id, etc).",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string"},
                    "fields": {"type": "object", "description": "Fields to update: title, directory, project_id, parent_id, time_archived"},
                },
                "required": ["session_id", "fields"],
            },
        ),
        Tool(
            name="oc_update_message",
            description="Update message data (JSON merge).",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "message_id": {"type": "string"},
                    "data_patch": {"type": "object", "description": "JSON fields to merge into data"},
                },
                "required": ["message_id", "data_patch"],
            },
        ),
        # ── DELETE tools ──
        Tool(
            name="oc_delete_messages",
            description="Delete messages and their parts by IDs. REQUIRES confirm=true. Creates backup.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "message_ids": {"type": "array", "items": {"type": "string"}, "description": "Message IDs to delete"},
                    "confirm": {"type": "boolean", "description": "Must be true to execute"},
                    "dry_run": {"type": "boolean", "description": "Show what would be deleted without doing it"},
                },
                "required": ["message_ids", "confirm"],
            },
        ),
        Tool(
            name="oc_delete_parts",
            description="Delete parts by IDs or by type filter. REQUIRES confirm=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string", "description": "Scope to session (optional)"},
                    "message_id": {"type": "string", "description": "Scope to message (optional)"},
                    "part_type": {"type": "string", "description": "Delete parts of this type: reasoning, step-start, step-finish, text, tool, patch"},
                    "part_ids": {"type": "array", "items": {"type": "string"}, "description": "Specific part IDs to delete"},
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["confirm"],
            },
        ),
        Tool(
            name="oc_compact_session",
            description="Smart cleanup: remove reasoning, steps, short continue messages. REQUIRES confirm=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string"},
                    "remove_reasoning": {"type": "boolean", "default": True, "description": "Remove reasoning parts"},
                    "remove_steps": {"type": "boolean", "default": True, "description": "Remove step-start/step-finish parts"},
                    "remove_short_user_msgs": {"type": "boolean", "default": True, "description": "Remove short 'continue' user messages"},
                    "min_short_len": {"type": "number", "default": 100, "description": "Max length for short message detection"},
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["session_id", "confirm"],
            },
        ),
        Tool(
            name="oc_truncate_session",
            description="Remove ALL messages and parts from session. Session metadata kept. REQUIRES confirm=true.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string"},
                    "session_id": {"type": "string"},
                    "confirm": {"type": "boolean"},
                    "dry_run": {"type": "boolean"},
                },
                "required": ["session_id", "confirm"],
            },
        ),
        # ── GIT TOOLS ──
        Tool(
            name="git_status",
            description="Show git status: modified, staged, untracked files.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string", "description": "Working directory (default: current)"},
                    "short": {"type": "boolean", "description": "Short format (default: false)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_diff",
            description="Show git diff (unified format).",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "file": {"type": "string", "description": "Specific file to diff"},
                    "staged": {"type": "boolean", "description": "Show staged changes"},
                    "limit": {"type": "number", "description": "Max lines of output"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_log",
            description="Recent git commits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "limit": {"type": "number", "default": 10},
                    "author": {"type": "string"},
                    "pattern": {"type": "string", "description": "Grep pattern for commit messages"},
                    "since": {"type": "string", "description": "Date like '7 days ago' or '2026-01-01'"},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_changed_files",
            description="Files changed in last N days.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "days": {"type": "number", "default": 7},
                    "pattern": {"type": "string", "description": "Glob pattern for files"},
                    "limit": {"type": "number", "default": 50},
                },
                "required": [],
            },
        ),
        Tool(
            name="file_frequency",
            description="Most frequently changed files in last N days.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "days": {"type": "number", "default": 30},
                    "limit": {"type": "number", "default": 20},
                },
                "required": [],
            },
        ),
        Tool(
            name="git_file_log",
            description="History of changes for a specific file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                    "file": {"type": "string", "description": "File path"},
                    "limit": {"type": "number", "default": 10},
                },
                "required": ["file"],
            },
        ),
        Tool(
            name="remote_check",
            description="Check if there are unpushed commits.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                },
                "required": [],
            },
        ),
        Tool(
            name="suggest_push",
            description="Show unpushed commits and files, suggest push.",
            inputSchema={
                "type": "object",
                "properties": {
                    "cwd": {"type": "string"},
                },
                "required": [],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    try:
        # ── READ ──
        if name == "oc_list_dbs":
            return _result(_list_db_files())

        elif name == "oc_list_sessions":
            return _handle_list_sessions(arguments)

        elif name == "oc_get_session":
            return _handle_get_session(arguments)

        elif name == "oc_read_messages":
            return _handle_read_messages(arguments)

        elif name == "oc_get_children":
            return _handle_get_children(arguments)

        elif name == "oc_check_orphans":
            return await _check_orphans()

        elif name == "oc_query":
            return _handle_query(arguments)

        # ── ANALYSIS ──
        elif name == "oc_health_check":
            return _handle_health_check(arguments)
        elif name == "oc_repair_wal":
            return _handle_repair_wal(arguments)
        elif name == "oc_analyze_session":
            return _handle_analyze(arguments)

        # ── BACKUP ──
        elif name == "oc_backup_db":
            db = arguments.get("db", "opencode.db")
            path = _backup_db(db)
            return _result({"backup_path": path, "status": "ok"})

        # ── UPDATE ──
        elif name == "oc_update_session":
            return _handle_update_session(arguments)

        elif name == "oc_update_message":
            return _handle_update_message(arguments)

        # ── DELETE ──
        elif name == "oc_delete_messages":
            return _handle_delete_messages(arguments)

        elif name == "oc_delete_parts":
            return _handle_delete_parts(arguments)

        elif name == "oc_compact_session":
            return _handle_compact(arguments)

        elif name == "oc_truncate_session":
            return _handle_truncate(arguments)

        elif name == "oc_exec_sql":
            return _handle_exec_sql(arguments)

        # ── ENCODING ──
        elif name == "oc_detect_mojibake":
            return _handle_detect_mojibake(arguments)

        elif name == "oc_fix_mojibake":
            return _handle_fix_mojibake(arguments)

        # ── GIT ──
        elif name == "git_status":
            return _handle_git_status(arguments)
        elif name == "git_diff":
            return _handle_git_diff(arguments)
        elif name == "git_log":
            return _handle_git_log(arguments)
        elif name == "git_changed_files":
            return _handle_git_changed_files(arguments)
        elif name == "file_frequency":
            return _handle_file_frequency(arguments)
        elif name == "git_file_log":
            return _handle_git_file_log(arguments)
        elif name == "remote_check":
            return _handle_remote_check(arguments)
        elif name == "suggest_push":
            return _handle_suggest_push(arguments)

        else:
            return _error(f"Unknown tool: {name}")

    except Exception as e:
        import traceback
        return _error(f"{e}\n{traceback.format_exc()}")


def _handle_health_check(args: dict) -> CallToolResult:
    """Deep health check for database files."""
    db_filter = args.get("db")
    base = BASE

    reports = []
    for f in sorted(base.glob("opencode*.db")):
        name = f.name
        if name.endswith(("-shm", "-wal")) or ".backup-" in name:
            continue
        if db_filter and db_filter not in name and db_filter != str(f):
            continue

        report = {"name": name, "path": str(f), "size_mb": round(f.stat().st_size / (1024*1024), 1)}

        # Check WAL/SHM
        wal = base / (name + "-wal")
        shm = base / (name + "-shm")
        report["wal_exists"] = wal.exists()
        report["shm_exists"] = shm.exists()
        if wal.exists():
            report["wal_size_mb"] = round(wal.stat().st_size / (1024*1024), 1)

        try:
            conn = sqlite3.connect(str(f), timeout=3)
            c = conn.cursor()

            # Integrity
            c.execute("PRAGMA integrity_check")
            ic = c.fetchone()[0]
            report["integrity"] = ic

            if ic != "ok":
                report["status"] = "MALFORMED"
                report["error"] = ic
                if report["wal_exists"] or report["shm_exists"]:
                    report["likely_cause"] = "WAL/SHM files from different schema"
                    report["fix"] = f"Delete {name}-wal and {name}-shm, then retry"
            else:
                report["status"] = "OK"

            # Schema
            c.execute("PRAGMA schema_version")
            report["schema_version"] = c.fetchone()[0]

            # Counts
            try:
                report["sessions"] = c.execute("SELECT COUNT(*) FROM session").fetchone()[0]
                report["messages"] = c.execute("SELECT COUNT(*) FROM message").fetchone()[0]
                report["parts"] = c.execute("SELECT COUNT(*) FROM part").fetchone()[0]
            except Exception:
                pass

            # Journal mode
            c.execute("PRAGMA journal_mode")
            report["journal_mode"] = c.fetchone()[0]

            conn.close()
        except sqlite3.DatabaseError as e:
            report["status"] = "MALFORMED"
            report["error"] = str(e)
            if report.get("wal_exists") or report.get("shm_exists"):
                report["likely_cause"] = "WAL/SHM files from different schema"
                report["fix"] = (
                    f"1. Stop opencode\n"
                    f"2. Delete: {name}-wal, {name}-shm\n"
                    f"3. If still broken, restore from backup\n"
                    f"4. To recover data: sqlite3 {name} .dump > recovered.sql"
                )
        except Exception as e:
            report["status"] = "ERROR"
            report["error"] = str(e)

        reports.append(report)

    return _result({"databases": reports, "count": len(reports)})


def _handle_repair_wal(args: dict) -> CallToolResult:
    """Remove stale WAL/SHM files."""
    if not _check_confirm(args):
        return _error("confirm=true required for WAL repair. It deletes WAL/SHM files.")

    db = args.get("db", "opencode.db")
    resolved = _resolve_db(db)
    dry_run = args.get("dry_run", False)

    wal_path = resolved + "-wal"
    shm_path = resolved + "-shm"
    actions = []

    # Create backup first
    if not dry_run:
        try:
            backup = _backup_db(db)
            actions.append(f"Backup created: {backup}")
        except Exception as e:
            actions.append(f"Backup failed: {e}")

    for path, label in [(wal_path, "WAL"), (shm_path, "SHM")]:
        if os.path.exists(path):
            size = os.path.getsize(path)
            if dry_run:
                actions.append(f"Would delete: {os.path.basename(path)} ({round(size/1024)} KB)")
            else:
                os.remove(path)
                actions.append(f"Deleted: {os.path.basename(path)} ({round(size/1024)} KB)")
        else:
            actions.append(f"Not found: {os.path.basename(path)} (nothing to do)")

    # Verify DB opens after repair
    if not dry_run:
        try:
            conn = sqlite3.connect(resolved, timeout=5)
            c = conn.cursor()
            c.execute("PRAGMA integrity_check")
            ic = c.fetchone()[0]
            c.execute("SELECT COUNT(*) FROM session")
            sessions = c.fetchone()[0]
            conn.close()
            actions.append(f"Verification: integrity={ic}, sessions={sessions}")
        except Exception as e:
            actions.append(f"Verification FAILED: {e}")

    return _result({"db": resolved, "dry_run": dry_run, "actions": actions})


# ════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════

def _result(data: Any) -> CallToolResult:
    return CallToolResult(content=[TextContent(
        type="text",
        text=json.dumps(data, ensure_ascii=False, default=str, indent=2)
    )])


def _error(msg: str) -> CallToolResult:
    return CallToolResult(content=[TextContent(
        type="text",
        text=json.dumps({"error": msg}, ensure_ascii=False, indent=2)
    )])


# ════════════════════════════════════════════════════════════════
# CP866 mojibake helpers
# ════════════════════════════════════════════════════════════════

# CP866 lookup: Unicode code point → CP866 byte value.
# Used to reverse the mojibake where UTF-8 bytes were misinterpreted as CP866.
_CP866_MAP = {
    # Uppercase А-Я (0x80-0x9F)
    0x0410: 0x80, 0x0411: 0x81, 0x0412: 0x82, 0x0413: 0x83, 0x0414: 0x84,
    0x0415: 0x85, 0x0416: 0x86, 0x0417: 0x87, 0x0418: 0x88, 0x0419: 0x89,
    0x041A: 0x8A, 0x041B: 0x8B, 0x041C: 0x8C, 0x041D: 0x8D, 0x041E: 0x8E,
    0x041F: 0x8F, 0x0420: 0x90, 0x0421: 0x91, 0x0422: 0x92, 0x0423: 0x93,
    0x0424: 0x94, 0x0425: 0x95, 0x0426: 0x96, 0x0427: 0x97, 0x0428: 0x98,
    0x0429: 0x99, 0x042A: 0x9A, 0x042B: 0x9B, 0x042C: 0x9C, 0x042D: 0x9D,
    0x042E: 0x9E, 0x042F: 0x9F,
    # Lowercase а-п (0xA0-0xAF)
    0x0430: 0xA0, 0x0431: 0xA1, 0x0432: 0xA2, 0x0433: 0xA3, 0x0434: 0xA4,
    0x0435: 0xA5, 0x0436: 0xA6, 0x0437: 0xA7, 0x0438: 0xA8, 0x0439: 0xA9,
    0x043A: 0xAA, 0x043B: 0xAB, 0x043C: 0xAC, 0x043D: 0xAD, 0x043E: 0xAE,
    0x043F: 0xAF,
    # Box drawing & shade (0xB0-0xDF)
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
    # Lowercase Ѐ-Џ (0xE0-0xEF)
    0x0440: 0xE0, 0x0441: 0xE1, 0x0442: 0xE2, 0x0443: 0xE3,
    0x0444: 0xE4, 0x0445: 0xE5, 0x0446: 0xE6, 0x0447: 0xE7,
    0x0448: 0xE8, 0x0449: 0xE9, 0x044A: 0xEA, 0x044B: 0xEB,
    0x044C: 0xEC, 0x044D: 0xED, 0x044E: 0xEE, 0x044F: 0xEF,
    # Misc (0xF0-0xFF)
    0x0401: 0xF0, 0x0451: 0xF1, 0x0404: 0xF2, 0x0454: 0xF3,
    0x0407: 0xF4, 0x0457: 0xF5, 0x040E: 0xF6, 0x045E: 0xF7,
    0x00B0: 0xF8, 0x2219: 0xF9, 0x00B7: 0xFA, 0x221A: 0xFB,
    0x207F: 0xFC, 0x00B2: 0xFD, 0x25A0: 0xFE, 0x00A0: 0xFF,
}


def _decode_cp866_mojibake(text: str) -> str:
    """Reverse CP866 mojibake: decode Unicode text whose code points
    represent CP866 characters back to the original UTF-8 string.
    """
    if not text:
        return text
    bytes_out = bytearray()
    for ch in text:
        cp = ord(ch)
        if 0x20 <= cp <= 0x7E:
            bytes_out.append(cp)
        elif cp in _CP866_MAP:
            bytes_out.append(_CP866_MAP[cp])
        else:
            bytes_out.append(0x3F)
    return bytes_out.decode("utf-8", errors="replace")


def _is_mojibake(text: str) -> bool:
    """Detect if text looks like CP866 mojibake
    (contains box-drawing U+2500+ chars mixed with Cyrillic)."""
    if not text:
        return False
    has_box = False
    has_suspect = False
    for ch in text:
        cp = ord(ch)
        if 0x2500 <= cp <= 0x257F:
            has_box = True
        if cp in _CP866_MAP:
            has_suspect = True
    if not (has_box and has_suspect):
        return False
    decoded = _decode_cp866_mojibake(text)
    return decoded != text and any(0x0400 <= ord(c) <= 0x04FF for c in decoded)


def _scan_for_mojibake(conn, table: str = "session") -> list[dict]:
    """Scan a table for mojibake titles/fields."""
    results = []
    id_col = {"session": "id", "message": "id", "part": "id"}[table]
    title_col = {"session": "title", "message": "content", "part": "content"}[table]

    try:
        rows = conn.execute(f"SELECT {id_col}, {title_col} FROM {table}").fetchall()
    except sqlite3.OperationalError:
        return []

    for row_id, content in rows:
        if not content or not isinstance(content, str):
            continue
        if _is_mojibake(content):
            decoded = _decode_cp866_mojibake(content)
            results.append({
                "id": row_id,
                "table": table,
                "original": content[:200],
                "decoded": decoded[:200],
            })
    return results


def _handle_detect_mojibake(arguments: dict) -> CallToolResult:
    """Detect CP866 mojibake across session/message/part tables."""
    db = arguments.get("db", "opencode.db")
    tables_to_scan = ["session"]

    try:
        conn = _connect(db)
    except Exception as e:
        return _error(f"Cannot connect to database: {e}")

    all_issues = []
    for table in tables_to_scan:
        issues = _scan_for_mojibake(conn, table)
        all_issues.extend(issues)

    conn.close()

    summary = {
        "database": db,
        "tables_scanned": tables_to_scan,
        "total_affected": len(all_issues),
        "affected_sessions": len([i for i in all_issues if i["table"] == "session"]),
        "issues": all_issues,
        "note": "Use oc_fix_mojibake to repair (requires confirm=true)",
    }
    return _result(summary)


def _handle_fix_mojibake(arguments: dict) -> CallToolResult:
    """Fix CP866 mojibake in session titles."""
    db = arguments.get("db", "opencode.db")
    dry_run = arguments.get("dry_run", False)
    confirmed = _check_confirm(arguments)

    if not confirmed and not dry_run:
        return _error("confirm=true required. Use dry_run=true to preview.")

    tables = arguments.get("table", "session")
    if tables == "all":
        tables_to_fix = ["session"]
    else:
        tables_to_fix = [tables]

    try:
        conn = _connect(db)
    except Exception as e:
        return _error(f"Cannot connect to database: {e}")

    all_issues = []
    for table in tables_to_fix:
        issues = _scan_for_mojibake(conn, table)
        all_issues.extend(issues)

    backup_path = None
    fixed_count = 0

    if not dry_run and all_issues:
        backup_path = _backup_db(db)

        for issue in all_issues:
            decoded = _decode_cp866_mojibake(issue["original"])
            t = issue["table"]
            id_col = {"session": "id", "message": "id", "part": "id"}[t]
            title_col = {"session": "title", "message": "content", "part": "content"}[t]
            conn.execute(
                f"UPDATE {t} SET {title_col} = ? WHERE {id_col} = ?",
                (decoded, issue["id"]),
            )
            fixed_count += 1
        conn.commit()

    conn.close()

    return _result({
        "database": db,
        "backup": backup_path,
        "dry_run": dry_run,
        "fixed": fixed_count,
        "total_found": len(all_issues),
        "details": [
            {
                "id": i["id"],
                "table": i["table"],
                "original": i["original"],
                "decoded": i["decoded"],
            }
            for i in all_issues
        ],
    })


# ════════════════════════════════════════════════════════════════
# Git helpers
# ════════════════════════════════════════════════════════════════
# READ handlers
# ════════════════════════════════════════════════════════════════

def _handle_list_sessions(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    conn = _connect(db)
    c = conn.cursor()
    sql = """SELECT id, title, project_id, parent_id, directory, time_created, time_updated,
             tokens_input, tokens_output, tokens_reasoning, cost FROM session WHERE 1=1"""
    params: list = []
    if args.get("project_id"):
        sql += " AND project_id = ?"
        params.append(args["project_id"])
    pid = args.get("parent_id")
    if pid == "null":
        sql += " AND parent_id IS NULL"
    elif pid == "any":
        sql += " AND parent_id IS NOT NULL"
    elif pid:
        sql += " AND parent_id = ?"
        params.append(pid)
    if args.get("search"):
        sql += " AND (title LIKE ? OR id LIKE ?)"
        params.extend([f"%{args['search']}%", f"%{args['search']}%"])
    sql += " ORDER BY time_updated DESC LIMIT ?"
    params.append(min(args.get("limit", 100), 5000))
    c.execute(sql, params)
    cols = [d[0] for d in c.description]
    result = [dict(zip(cols, r)) for r in c.fetchall()]
    for r in result:
        r["children"] = conn.execute("SELECT COUNT(*) FROM session WHERE parent_id=?", (r["id"],)).fetchone()[0]
    conn.close()
    return _result(result)


def _handle_get_session(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    sid = args["session_id"]
    conn = _connect(db)
    c = conn.cursor()
    c.execute("""SELECT id, title, project_id, parent_id, slug, directory, path, version,
                 agent, model, time_created, time_updated, time_archived,
                 tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, cost, metadata
                 FROM session WHERE id = ?""", (sid,))
    row = c.fetchone()
    if not row:
        conn.close()
        return _error("Session not found")
    cols = ["id", "title", "project_id", "parent_id", "slug", "directory", "path", "version",
            "agent", "model", "time_created", "time_updated", "time_archived",
            "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write", "cost", "metadata"]
    session = dict(zip(cols, row))
    c.execute("SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created", (sid,))
    messages = []
    for m in c.fetchall():
        msg = {"id": m[0], "time": m[1], "data": json.loads(m[2]) if m[2] else {}}
        c.execute("SELECT id, data FROM part WHERE message_id=? ORDER BY time_created", (m[0],))
        msg["parts"] = [json.loads(p[1]) | {"part_id": p[0]} for p in c.fetchall()]
        msg["part_count"] = len(msg["parts"])
        messages.append(msg)
    session["messages"] = messages
    session["message_count"] = len(messages)
    conn.close()
    return _result(session)


def _handle_read_messages(args: dict) -> CallToolResult:
    """Read messages in clean format. Supports by session, by message_id, or last N messages."""
    db = args.get("db", "opencode.db")
    conn = _connect(db)
    c = conn.cursor()

    # Determine session_id
    sid = args.get("session_id")
    if not sid:
        if args.get("message_id"):
            # Find session from message
            c.execute("SELECT session_id FROM message WHERE id=?", (args["message_id"],))
            row = c.fetchone()
            if not row:
                conn.close()
                return _error("Message not found")
            sid = row[0]
        else:
            # Latest session
            c.execute("SELECT id FROM session ORDER BY time_updated DESC LIMIT 1")
            row = c.fetchone()
            if not row:
                conn.close()
                return _error("No sessions found")
            sid = row[0]

    # Get session title
    c.execute("SELECT title, time_created, time_updated FROM session WHERE id=?", (sid,))
    srow = c.fetchone()
    session_info = {
        "session_id": sid,
        "title": srow[0] if srow else "?",
        "time_created": srow[1] if srow else 0,
    }

    # Build message query
    message_id = args.get("message_id")
    limit = args.get("limit")
    offset = args.get("offset")
    include_parts = args.get("include_parts", False)
    tool_type = args.get("tool_type", "")
    file_path = args.get("file_path", "")

    if message_id:
        # Single message by ID
        c.execute("SELECT id, time_created, data FROM message WHERE id=? ORDER BY time_created", (message_id,))
        raw_messages = c.fetchall()
    elif offset is not None:
        # Offset-based: skip first N messages chronologically, optionally limit
        if limit:
            c.execute("""SELECT id, time_created, data FROM message
                         WHERE session_id=? ORDER BY time_created ASC LIMIT ? OFFSET ?""", (sid, limit, offset))
        else:
            c.execute("""SELECT id, time_created, data FROM message
                         WHERE session_id=? ORDER BY time_created ASC LIMIT 999999 OFFSET ?""", (sid, offset))
        raw_messages = c.fetchall()
    elif limit:
        # Last N messages (in reverse, then flip)
        c.execute("""SELECT id, time_created, data FROM message
                     WHERE session_id=? ORDER BY time_created DESC LIMIT ?""", (sid, limit))
        raw_messages = c.fetchall()
        raw_messages.reverse()  # Put back in chronological order
    else:
        # All messages
        c.execute("SELECT id, time_created, data FROM message WHERE session_id=? ORDER BY time_created", (sid,))
        raw_messages = c.fetchall()

    messages = []
    for mid, tcreated, data_raw in raw_messages:
        data = json.loads(data_raw) if data_raw else {}
        role = data.get("role", "?")
        model_id = data.get("modelID", "")

        # Get parts for this message
        c.execute("SELECT id, data FROM part WHERE message_id=? ORDER BY time_created", (mid,))
        parts_raw = c.fetchall()

        text_content = ""
        tool_calls = []
        reasoning = ""
        step_info = []

        for pid, pdata_raw in parts_raw:
            pdata = json.loads(pdata_raw) if pdata_raw else {}
            ptype = pdata.get("type", "")

            # Filter by tool_type
            if tool_type and ptype == "tool":
                actual_tool = pdata.get("tool", "")
                if actual_tool != tool_type:
                    continue

            # Filter by file_path (for edit tools)
            if file_path and ptype == "tool":
                actual_tool = pdata.get("tool", "")
                tool_file = pdata.get("state", {}).get("input", {}).get("filePath", "")
                if actual_tool == "edit" and file_path not in tool_file:
                    continue

            if ptype == "text":
                text_content += (pdata.get("text", "") or "")
            elif ptype == "reasoning":
                reasoning += (pdata.get("text", "") or "")
            elif ptype == "tool":
                tool_name = pdata.get("tool", "")
                tool_input = pdata.get("state", {}).get("input", {})
                tool_output = pdata.get("state", {}).get("output", "")
                tool_status = pdata.get("state", {}).get("status", "")
                tool_calls.append({
                    "tool": tool_name,
                    "input": tool_input,
                    "output_preview": (tool_output[:500] + "...") if len(tool_output) > 500 else tool_output,
                    "status": tool_status,
                })
            elif ptype == "step-start":
                step_info.append("step-start")
            elif ptype == "step-finish":
                reason = pdata.get("reason", "")
                step_info.append(f"step-finish ({reason})")

        msg_entry = {
            "id": mid,
            "role": role,
            "time": tcreated,
            "model": model_id,
            "content": text_content,
        }

        if reasoning:
            msg_entry["reasoning"] = reasoning[:1000] + ("..." if len(reasoning) > 1000 else "")

        if tool_calls:
            msg_entry["tool_calls"] = tool_calls

        if include_parts:
            msg_entry["parts"] = [
                {"id": p[0], "data": json.loads(p[1]) if p[1] else {}}
                for p in parts_raw
            ]

        # For assistant messages with no text but with tool calls, show summary
        if role == "assistant" and not text_content and tool_calls:
            msg_entry["content"] = f"[Tool calls: {', '.join(t['tool'] for t in tool_calls)}]"

        # For assistant messages with finish reason, show it
        finish = data.get("finish", "")
        if finish:
            msg_entry["finish_reason"] = finish

        messages.append(msg_entry)

    conn.close()

    result = {
        "session": session_info,
        "message_count": len(messages),
        "messages": messages,
    }

    return _result(result)


def _handle_get_children(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    pid = args["parent_id"]
    recursive = args.get("recursive", True)
    conn = _connect(db)
    c = conn.cursor()
    cols = ["id", "title", "project_id", "parent_id", "directory", "time_created", "time_updated",
            "tokens_input", "tokens_output", "tokens_reasoning"]
    c.execute(f"SELECT {','.join(cols)} FROM session WHERE parent_id=? ORDER BY time_created", (pid,))
    result = [dict(zip(cols, r)) for r in c.fetchall()]
    if recursive:
        def nested(parent_sid: str, depth: int = 0):
            if depth > 10:
                return []
            c2 = conn.cursor()
            c2.execute("SELECT id FROM session WHERE parent_id=?", (parent_sid,))
            out = []
            for (nid,) in c2.fetchall():
                c2.execute(f"SELECT {','.join(cols)} FROM session WHERE id=?", (nid,))
                r2 = c2.fetchone()
                if r2:
                    item = dict(zip(cols, r2))
                    item["_depth"] = depth + 1
                    item["children_nested"] = nested(nid, depth + 1)
                    out.append(item)
            return out
        for child in result:
            child["children_nested"] = nested(child["id"])
    conn.close()
    return _result(result)


def _handle_query(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    sql = args["sql"].strip()
    upper = sql.upper()
    if not (upper.startswith("SELECT") or upper.startswith("EXPLAIN") or upper.startswith("PRAGMA")):
        return _error("Only SELECT/EXPLAIN/PRAGMA allowed in oc_query. Use oc_exec_sql for writes.")
    conn = _connect(db)
    c = conn.cursor()
    c.execute(sql)
    cols = [d[0] for d in c.description] if c.description else []
    rows = c.fetchall()
    conn.close()
    return _result({"columns": cols, "rows": rows, "count": len(rows)})


# ════════════════════════════════════════════════════════════════
# ANALYSIS
# ════════════════════════════════════════════════════════════════

def _handle_analyze(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    sid = args["session_id"]
    conn = _connect(db)
    c = conn.cursor()

    # Session metadata
    c.execute("SELECT title, time_created, time_updated, tokens_input, tokens_output FROM session WHERE id=?", (sid,))
    meta = c.fetchone()
    if not meta:
        conn.close()
        return _error("Session not found")

    result = {
        "session_id": sid,
        "title": meta[0],
        "time_range": {"created": meta[1], "updated": meta[2]},
        "tokens": {"input": meta[3], "output": meta[4]},
    }

    # Message stats
    c.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (sid,))
    total_msgs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM message WHERE session_id=? AND json_extract(data,'$.role')='user'", (sid,))
    user_msgs = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM message WHERE session_id=? AND json_extract(data,'$.role')='assistant'", (sid,))
    asst_msgs = c.fetchone()[0]
    result["messages"] = {"total": total_msgs, "user": user_msgs, "assistant": asst_msgs}

    # Parts by type
    c.execute("""SELECT json_extract(data,'$.type') as t, COUNT(*), SUM(LENGTH(data))
                 FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?) GROUP BY t""",
              (sid,))
    parts = {}
    total_bytes = 0
    for row in c.fetchall():
        parts[row[0]] = {"count": row[1], "bytes": row[2] or 0}
        total_bytes += row[2] or 0
    result["parts"] = parts
    result["total_bytes"] = total_bytes

    # Redundancy analysis
    redundancy = {}

    # Short user messages
    c.execute("""SELECT m.id, SUBSTR(json_extract(p.data,'$.text'),1,80) as txt,
                 LENGTH(json_extract(p.data,'$.text')) as len
                 FROM message m JOIN part p ON p.message_id=m.id
                 WHERE m.session_id=? AND json_extract(m.data,'$.role')='user'
                 AND json_extract(p.data,'$.type')='text'
                 AND LENGTH(json_extract(p.data,'$.text')) < 100""", (sid,))
    short = [{"id": r[0], "text": r[1], "length": r[2]} for r in c.fetchall()]
    redundancy["short_user_messages"] = {"count": len(short), "examples": short[:5]}
    redundancy["short_user_messages_savings_bytes"] = sum(r["length"] for r in short) * 3  # approx with parts

    # Reasoning parts
    c.execute("""SELECT COUNT(*), SUM(LENGTH(data))
                 FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                 AND json_extract(data,'$.type')='reasoning'""", (sid,))
    r = c.fetchone()
    redundancy["reasoning"] = {"count": r[0], "bytes": r[1] or 0}

    # Step parts
    c.execute("""SELECT COUNT(*), SUM(LENGTH(data))
                 FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                 AND json_extract(data,'$.type') IN ('step-start','step-finish')""", (sid,))
    r = c.fetchone()
    redundancy["steps"] = {"count": r[0], "bytes": r[1] or 0}

    # Large tool outputs
    c.execute("""SELECT p.id, json_extract(p.data,'$.tool') as tool,
                 LENGTH(json_extract(p.data,'$.state.output')) as out_len
                 FROM part p WHERE p.message_id IN (SELECT id FROM message WHERE session_id=?)
                 AND json_extract(p.data,'$.type')='tool'
                 AND LENGTH(json_extract(p.data,'$.state.output')) > 5000
                 ORDER BY out_len DESC LIMIT 10""", (sid,))
    large = [{"part_id": r[0], "tool": r[1], "output_bytes": r[2]} for r in c.fetchall()]
    redundancy["large_tool_outputs"] = {"count": len(large), "examples": large}

    # Potential savings
    savings = (redundancy["reasoning"]["bytes"] +
               redundancy["steps"]["bytes"] +
               redundancy["short_user_messages_savings_bytes"])
    redundancy["potential_savings_bytes"] = savings

    result["redundancy"] = redundancy
    conn.close()
    return _result(result)


# ════════════════════════════════════════════════════════════════
# UPDATE
# ════════════════════════════════════════════════════════════════

def _handle_update_session(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    sid = args["session_id"]
    fields = args["fields"]
    allowed = {"title", "directory", "project_id", "parent_id", "time_archived", "slug", "path", "agent", "model"}
    invalid = set(fields.keys()) - allowed
    if invalid:
        return _error(f"Invalid fields: {invalid}. Allowed: {allowed}")
    conn = _connect(db)
    c = conn.cursor()
    c.execute("SELECT id FROM session WHERE id=?", (sid,))
    if not c.fetchone():
        conn.close()
        return _error("Session not found")
    sets = []
    vals = []
    for k, v in fields.items():
        sets.append(f"{k} = ?")
        vals.append(v)
    vals.append(sid)
    c.execute(f"UPDATE session SET {', '.join(sets)} WHERE id=?", vals)
    conn.commit()
    conn.close()
    return _result({"updated": sid, "fields": fields, "status": "ok"})


def _handle_update_message(args: dict) -> CallToolResult:
    db = args.get("db", "opencode.db")
    mid = args["message_id"]
    patch = args["data_patch"]
    conn = _connect(db)
    c = conn.cursor()
    c.execute("SELECT data FROM message WHERE id=?", (mid,))
    row = c.fetchone()
    if not row:
        conn.close()
        return _error("Message not found")
    current = json.loads(row[0]) if row[0] else {}
    current.update(patch)
    c.execute("UPDATE message SET data=? WHERE id=?", (json.dumps(current, ensure_ascii=False), mid))
    conn.commit()
    conn.close()
    return _result({"updated": mid, "merged_keys": list(patch.keys()), "status": "ok"})


# ════════════════════════════════════════════════════════════════
# DELETE
# ════════════════════════════════════════════════════════════════

def _handle_delete_messages(args: dict) -> CallToolResult:
    if not _check_confirm(args):
        return _error("confirm=true required for destructive operations")
    db = args.get("db", "opencode.db")
    ids = args["message_ids"]
    dry = args.get("dry_run", False)

    if dry:
        conn = _connect(db)
        c = conn.cursor()
        plan = []
        for mid in ids:
            c.execute("SELECT LENGTH(data) FROM message WHERE id=?", (mid,))
            r = c.fetchone()
            msg_size = r[0] if r else 0
            c.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0) FROM part WHERE message_id=?", (mid,))
            pc, ps = c.fetchone()
            plan.append({"id": mid, "msg_bytes": msg_size, "parts": pc, "parts_bytes": ps})
        conn.close()
        return _result({"dry_run": True, "would_delete": plan, "total_messages": len(ids)})

    backup = _backup_db(db)
    conn = _connect(db)
    c = conn.cursor()
    deleted = {"messages": 0, "parts": 0, "bytes_freed": 0}
    for mid in ids:
        c.execute("SELECT LENGTH(data) FROM message WHERE id=?", (mid,))
        r = c.fetchone()
        msg_size = r[0] if r else 0
        c.execute("SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0) FROM part WHERE message_id=?", (mid,))
        pc, ps = c.fetchone()
        c.execute("DELETE FROM part WHERE message_id=?", (mid,))
        c.execute("DELETE FROM message WHERE id=?", (mid,))
        deleted["messages"] += 1
        deleted["parts"] += pc
        deleted["bytes_freed"] += msg_size + ps
    conn.commit()
    conn.close()
    deleted["backup"] = backup
    deleted["status"] = "ok"
    return _result(deleted)


def _handle_delete_parts(args: dict) -> CallToolResult:
    if not _check_confirm(args):
        return _error("confirm=true required")
    db = args.get("db", "opencode.db")
    dry = args.get("dry_run", False)

    # Build WHERE clause
    conditions = []
    params = []
    if args.get("part_ids"):
        placeholders = ",".join("?" * len(args["part_ids"]))
        conditions.append(f"id IN ({placeholders})")
        params.extend(args["part_ids"])
    else:
        if args.get("session_id"):
            conditions.append("message_id IN (SELECT id FROM message WHERE session_id=?)")
            params.append(args["session_id"])
        elif args.get("message_id"):
            conditions.append("message_id=?")
            params.append(args["message_id"])
        if args.get("part_type"):
            conditions.append("json_extract(data,'$.type')=?")
            params.append(args["part_type"])

    if not conditions:
        return _error("Must specify session_id, message_id, part_ids, or part_type")

    where = " AND ".join(conditions)

    if dry:
        conn = _connect(db)
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0) FROM part WHERE {where}", params)
        cnt, total = c.fetchone()
        conn.close()
        return _result({"dry_run": True, "would_delete_parts": cnt, "bytes": total})

    backup = _backup_db(db)
    conn = _connect(db)
    c = conn.cursor()
    c.execute(f"SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0) FROM part WHERE {where}", params)
    cnt, total = c.fetchone()
    c.execute(f"DELETE FROM part WHERE {where}", params)
    conn.commit()
    conn.close()
    return _result({"deleted_parts": cnt, "bytes_freed": total, "backup": backup, "status": "ok"})


def _handle_compact(args: dict) -> CallToolResult:
    if not _check_confirm(args):
        return _error("confirm=true required")
    db = args.get("db", "opencode.db")
    sid = args["session_id"]
    dry = args.get("dry_run", False)
    remove_reasoning = args.get("remove_reasoning", True)
    remove_steps = args.get("remove_steps", True)
    remove_short = args.get("remove_short_user_msgs", True)
    min_short = args.get("min_short_len", 100)

    conn = _connect(db)
    c = conn.cursor()
    plan = {"reasoning_parts": 0, "step_parts": 0, "short_messages": 0, "total_bytes": 0}

    if remove_reasoning:
        c.execute("""SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0)
                     FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                     AND json_extract(data,'$.type')='reasoning'""", (sid,))
        r = c.fetchone()
        plan["reasoning_parts"] = r[0]
        plan["total_bytes"] += r[1] or 0

    if remove_steps:
        c.execute("""SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0)
                     FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                     AND json_extract(data,'$.type') IN ('step-start','step-finish')""", (sid,))
        r = c.fetchone()
        plan["step_parts"] = r[0]
        plan["total_bytes"] += r[1] or 0

    short_ids = []
    if remove_short:
        c.execute("""SELECT m.id, LENGTH(m.data) + COALESCE(
                        (SELECT SUM(LENGTH(data)) FROM part WHERE message_id=m.id), 0) as total
                     FROM message m JOIN part p ON p.message_id=m.id
                     WHERE m.session_id=? AND json_extract(m.data,'$.role')='user'
                     AND json_extract(p.data,'$.type')='text'
                     AND LENGTH(json_extract(p.data,'$.text')) < ?""", (sid, min_short))
        for row in c.fetchall():
            short_ids.append(row[0])
            plan["short_messages"] += 1
            plan["total_bytes"] += row[1] or 0

    if dry:
        conn.close()
        return _result({"dry_run": True, "session_id": sid, "plan": plan, "short_message_ids": short_ids[:10]})

    backup = _backup_db(db)
    deleted_parts = 0
    deleted_msgs = 0

    if remove_reasoning:
        c.execute("""DELETE FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                     AND json_extract(data,'$.type')='reasoning'""", (sid,))
        deleted_parts += c.rowcount

    if remove_steps:
        c.execute("""DELETE FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)
                     AND json_extract(data,'$.type') IN ('step-start','step-finish')""", (sid,))
        deleted_parts += c.rowcount

    for mid in short_ids:
        c.execute("DELETE FROM part WHERE message_id=?", (mid,))
        c.execute("DELETE FROM message WHERE id=?", (mid,))
        deleted_msgs += 1

    conn.commit()
    conn.close()
    return _result({
        "status": "ok",
        "session_id": sid,
        "deleted_parts": deleted_parts,
        "deleted_messages": deleted_msgs,
        "bytes_freed": plan["total_bytes"],
        "backup": backup,
    })


def _handle_truncate(args: dict) -> CallToolResult:
    if not _check_confirm(args):
        return _error("confirm=true required")
    db = args.get("db", "opencode.db")
    sid = args["session_id"]
    dry = args.get("dry_run", False)

    conn = _connect(db)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM message WHERE session_id=?", (sid,))
    msg_count = c.fetchone()[0]
    c.execute("""SELECT COUNT(*), COALESCE(SUM(LENGTH(data)),0)
                 FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)""", (sid,))
    part_count, part_bytes = c.fetchone()

    if dry:
        conn.close()
        return _result({"dry_run": True, "would_delete": {"messages": msg_count, "parts": part_count, "bytes": part_bytes}})

    backup = _backup_db(db)
    c.execute("DELETE FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)", (sid,))
    c.execute("DELETE FROM message WHERE session_id=?", (sid,))
    conn.commit()
    conn.close()
    return _result({"status": "ok", "deleted": {"messages": msg_count, "parts": part_count, "bytes": part_bytes}, "backup": backup})


def _handle_exec_sql(args: dict) -> CallToolResult:
    if not _check_confirm(args):
        return _error("confirm=true required for arbitrary SQL")
    db = args.get("db", "opencode.db")
    sql = args["sql"]
    params = args.get("params", [])
    dry = args.get("dry_run", False)

    if dry:
        return _result({"dry_run": True, "sql": sql, "params": params})

    backup = _backup_db(db)
    conn = _connect(db)
    c = conn.cursor()
    c.execute(sql, params)
    affected = c.rowcount
    conn.commit()
    # Try to return results if SELECT-like
    result_data = None
    if c.description:
        cols = [d[0] for d in c.description]
        rows = c.fetchall()
        result_data = {"columns": cols, "rows": rows}
    conn.close()
    return _result({"status": "ok", "affected_rows": affected, "backup": backup, "result": result_data})


# ════════════════════════════════════════════════════════════════
# ORPHANS check
# ════════════════════════════════════════════════════════════════

async def _check_orphans() -> CallToolResult:
    dbs = _list_db_files()
    prod_path = str(BASE / "opencode.db")
    dev_path = str(BASE / "opencode-dev.db")
    result = {"databases_found": dbs, "comparison": {}}

    for label, path in [("opencode.db", prod_path), ("opencode-dev.db", dev_path)]:
        if not os.path.exists(path):
            result["comparison"][label] = {"error": "NOT_FOUND"}
            continue
        conn = sqlite3.connect(path, timeout=2)
        c = conn.cursor()
        total = c.execute("SELECT COUNT(*) FROM session").fetchone()[0]
        roots = c.execute("SELECT COUNT(*) FROM session WHERE parent_id IS NULL").fetchone()[0]
        children = c.execute("SELECT COUNT(*) FROM session WHERE parent_id IS NOT NULL").fetchone()[0]
        archived = c.execute("SELECT COUNT(*) FROM session WHERE time_archived IS NOT NULL").fetchone()[0]
        orphans = c.execute("""SELECT COUNT(*) FROM session s1
            WHERE s1.parent_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)""").fetchone()[0]
        c.execute("SELECT project_id, COUNT(*) as cnt FROM session GROUP BY project_id ORDER BY cnt DESC")
        projects = {r[0]: r[1] for r in c.fetchall()}
        result["comparison"][label] = {
            "total": total, "root": roots, "children": children,
            "archived": archived, "orphan_children": orphans, "by_project": projects,
        }
        conn.close()

    if os.path.exists(prod_path) and os.path.exists(dev_path):
        cp = sqlite3.connect(prod_path, timeout=2)
        cd = sqlite3.connect(dev_path, timeout=2)
        pids = set(r[0] for r in cp.execute("SELECT id FROM session").fetchall())
        dids = set(r[0] for r in cd.execute("SELECT id FROM session").fetchall())
        result["cross_db"] = {
            "only_in_opencode_db": list(pids - dids),
            "only_in_opencode_dev_db": list(dids - pids),
            "in_both": len(pids & dids),
        }
        common = pids & dids
        mismatches = []
        for sid in common:
            pp = cp.execute("SELECT project_id FROM session WHERE id=?", (sid,)).fetchone()[0]
            dp = cd.execute("SELECT project_id FROM session WHERE id=?", (sid,)).fetchone()[0]
            if pp != dp:
                mismatches.append({"id": sid, "opencode.db": pp, "opencode-dev.db": dp})
        if mismatches:
            result["cross_db"]["project_id_mismatches"] = mismatches
        cp.close()
        cd.close()

    return _result(result)


# ════════════════════════════════════════════════════════════════
# GIT handlers
# ════════════════════════════════════════════════════════════════

import subprocess

def _git(cwd: str | None, *args: str) -> tuple[str, int]:
    """Run git command, return (stdout, returncode)."""
    try:
        r = subprocess.run(
            ["git"] + list(args),
            cwd=cwd or ".",
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return f"git error: {e}", 1


def _git_or_error(cwd: str | None, *args: str) -> CallToolResult:
    """Run git command, return error if failed."""
    out, code = _git(cwd, *args)
    if code != 0:
        return _error(f"git {args[0]} failed (rc={code}): {out}")
    return _result({"output": out})


def _handle_git_status(args: dict) -> CallToolResult:
    """git status — modified, staged, untracked."""
    cwd = args.get("cwd")
    short = args.get("short", False)
    cmd = ["status"]
    if short:
        cmd.append("--short")
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git status failed: {out}")

    # Parse short format
    if short:
        lines = out.splitlines() if out else []
        files = []
        for line in lines:
            if len(line) >= 3:
                status = line[:2].strip()
                path = line[3:]
                files.append({"status": status, "path": path})
        return _result({
            "short": True,
            "count": len(files),
            "files": files,
        })

    # Long format — parse sections
    result = {"untracked": [], "modified": [], "staged": [], "branch": None}
    section = None
    for line in (out or "").splitlines():
        if line.startswith("On branch "):
            result["branch"] = line[10:]
        elif "Changes not staged" in line:
            section = "modified"
        elif "Changes to be committed" in line:
            section = "staged"
        elif "Untracked files" in line:
            section = "untracked"
        elif line.strip().startswith("modified:") or line.strip().startswith("new file:") or line.strip().startswith("deleted:"):
            path = line.strip().split(":", 1)[1].strip()
            if section:
                result[section].append(path)
        elif section == "untracked" and line.strip() and not line.startswith("("):
            result["untracked"].append(line.strip())

    return _result(result)


def _handle_git_diff(args: dict) -> CallToolResult:
    """git diff — unified format."""
    cwd = args.get("cwd")
    staged = args.get("staged", False)
    file = args.get("file")
    limit = args.get("limit", 500)
    cmd = ["diff"]
    if staged:
        cmd.append("--staged")
    if file:
        cmd.extend(["--", file])
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git diff failed: {out}")
    lines = out.splitlines()
    truncated = len(lines) > limit
    return _result({
        "diff": "\n".join(lines[:limit]),
        "truncated": truncated,
        "total_lines": len(lines),
    })


def _handle_git_log(args: dict) -> CallToolResult:
    """git log — recent commits."""
    cwd = args.get("cwd")
    limit = args.get("limit", 10)
    author = args.get("author")
    pattern = args.get("pattern")
    since = args.get("since")
    cmd = ["log", f"-{limit}", "--pretty=format:%H|%s|%an|%ai"]
    if author:
        cmd.extend(["--author", author])
    if pattern:
        cmd.extend(["--grep", pattern])
    if since:
        cmd.extend(["--since", since])
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git log failed: {out}")
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0][:8],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return _result({
        "count": len(commits),
        "commits": commits,
    })


def _handle_git_changed_files(args: dict) -> CallToolResult:
    """git log --name-only — files changed in last N days."""
    cwd = args.get("cwd")
    days = args.get("days", 7)
    pattern = args.get("pattern")
    limit = args.get("limit", 50)
    cmd = ["log", f"--since={days} days ago", "--pretty=format:", "--name-only"]
    if pattern:
        cmd.extend(["--", pattern])
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git log failed: {out}")
    seen = []
    seen_set = set()
    for line in (out or "").splitlines():
        line = line.strip()
        if line and line not in seen_set:
            seen_set.add(line)
            seen.append(line)
    return _result({
        "days": days,
        "count": len(seen[:limit]),
        "files": seen[:limit],
    })


def _handle_file_frequency(args: dict) -> CallToolResult:
    """git log --name-only — count file changes."""
    cwd = args.get("cwd")
    days = args.get("days", 30)
    limit = args.get("limit", 20)
    cmd = ["log", f"--since={days} days ago", "--pretty=format:", "--name-only"]
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git log failed: {out}")
    freq: dict[str, int] = {}
    for line in (out or "").splitlines():
        line = line.strip()
        if line:
            freq[line] = freq.get(line, 0) + 1
    sorted_files = sorted(freq.items(), key=lambda x: -x[1])[:limit]
    return _result({
        "days": days,
        "files": [{"path": f, "changes": c} for f, c in sorted_files],
    })


def _handle_git_file_log(args: dict) -> CallToolResult:
    """git log --follow — history of a file."""
    cwd = args.get("cwd")
    file = args.get("file")
    limit = args.get("limit", 10)
    cmd = ["log", f"-{limit}", "--pretty=format:%H|%s|%an|%ai", "--follow", "--", file]
    out, code = _git(cwd, *cmd)
    if code != 0:
        return _error(f"git log failed: {out}")
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("|", 3)
        if len(parts) == 4:
            commits.append({
                "hash": parts[0][:8],
                "message": parts[1],
                "author": parts[2],
                "date": parts[3],
            })
    return _result({
        "file": file,
        "count": len(commits),
        "commits": commits,
    })


def _handle_remote_check(args: dict) -> CallToolResult:
    """git status --short + remote tracking comparison."""
    cwd = args.get("cwd")
    # Check unpushed commits
    out, code = _git(cwd, "log", "@{u}..HEAD", "--pretty=format:%H|%s|%ai")
    if code != 0:
        # No upstream
        out2, code2 = _git(cwd, "branch", "--show-current")
        return _result({
            "ahead": None,
            "no_upstream": True,
            "branch": out2.strip() if code2 == 0 else "unknown",
        })
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({
                "hash": parts[0][:8],
                "message": parts[1],
                "date": parts[2],
            })
    # Check uncommitted changes
    st, _ = _git(cwd, "status", "--short")
    files_changed = len([l for l in st.splitlines() if l.strip()]) if st else 0
    return _result({
        "ahead": len(commits),
        "unpushed_commits": commits,
        "uncommitted_files": files_changed,
    })


def _handle_suggest_push(args: dict) -> CallToolResult:
    """Suggest push: show unpushed commits + files."""
    cwd = args.get("cwd")
    out, code = _git(cwd, "log", "@{u}..HEAD", "--pretty=format:%H|%s|%ai")
    if code != 0:
        return _result({"suggestion": "No upstream — push manually with `git push -u origin <branch>`"})
    commits = []
    for line in (out or "").splitlines():
        parts = line.split("|", 2)
        if len(parts) == 3:
            commits.append({"hash": parts[0][:8], "message": parts[1], "date": parts[2]})
    # Files in unpushed commits
    files_out, _ = _git(cwd, "diff", "--name-only", "@{u}..HEAD")
    files = [f for f in (files_out or "").splitlines() if f.strip()]
    st, _ = _git(cwd, "status", "--short")
    uncommitted = len([l for l in st.splitlines() if l.strip()]) if st else 0
    return _result({
        "ahead": len(commits),
        "unpushed_commits": commits,
        "files_to_push": files,
        "uncommitted_files": uncommitted,
        "suggestion": f"git push ({len(commits)} commits, {len(files)} files)" if commits else "Nothing to push",
    })


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
