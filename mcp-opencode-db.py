#!/usr/bin/env python3
"""MCP сервер для инспекции SQLite-баз OpenCode.

Doc-ID: MCP-OCDB-1
Дата: 2026-06-02
Связанные: [ARCH-v2], [DB-SELECTOR-1]

Протокол: MCP stdio (совместим с opencode MCP).
Инструменты: список, инспекция, сравнение БД, read-only SQL.
"""

from pathlib import Path
from typing import Any
import json
import os
import sqlite3

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, CallToolResult


BASE = Path.home() / ".local" / "share" / "opencode"
server = Server("opencode-db-inspector")


def _list_db_files() -> list[dict[str, Any]]:
    """Scan all opencode*.db files in the data directory."""
    dbs = []
    for f in sorted(BASE.glob("opencode*.db")):
        name = f.name
        if name.endswith(("-shm", "-wal")) or ".backup-" in name:
            continue
        try:
            conn = sqlite3.connect(str(f), timeout=2)
            c = conn.cursor()
            sessions = c.execute("SELECT COUNT(*) FROM session").fetchone()[0]
            conn.close()
        except Exception:
            sessions = -1
        dbs.append({"path": str(f), "name": name, "sessions": sessions, "size": f.stat().st_size})
    return dbs


def _resolve_db(db: str) -> str:
    """Resolve short name to full path."""
    for d in _list_db_files():
        if db in (d["name"], d["path"], d["name"].replace(".db", "")):
            return d["path"]
    return str(BASE / db) if not db.startswith(str(BASE)) else db


def _connect(db_path: str, readonly: bool = True) -> sqlite3.Connection:
    """Open connection with short timeout (avoid hanging)."""
    return sqlite3.connect(_resolve_db(db_path), timeout=3)


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="oc_list_dbs",
            description="List all opencode SQLite databases (path, sessions, size).",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="oc_list_sessions",
            description="List sessions from a database with optional filters.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name or path (default: opencode.db)"},
                    "project_id": {"type": "string", "description": "Filter by project_id"},
                    "parent_id": {"type": "string", "description": "parent_id filter: 'null' for roots, 'any' for children, or exact ID"},
                    "search": {"type": "string", "description": "Search in title and ID"},
                    "limit": {"type": "number", "description": "Max results (default: 100)"},
                },
                "required": [],
            },
        ),
        Tool(
            name="oc_get_session",
            description="Get full session details: messages, parts, metadata.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name or path"},
                    "session_id": {"type": "string", "description": "Session ID (ses_...)"},
                },
                "required": ["session_id"],
            },
        ),
        Tool(
            name="oc_get_children",
            description="Get all child sessions for a given parent session.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name or path"},
                    "parent_id": {"type": "string", "description": "Parent session ID"},
                    "recursive": {"type": "boolean", "description": "Include nested children recursively (default: true)"},
                },
                "required": ["parent_id"],
            },
        ),
        Tool(
            name="oc_check_orphans",
            description="Compare opencode.db vs opencode-dev.db. Find sessions missing in one, orphaned children, project_id mismatches.",
            inputSchema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        ),
        Tool(
            name="oc_query",
            description="Run a read-only SQL query against a database.",
            inputSchema={
                "type": "object",
                "properties": {
                    "db": {"type": "string", "description": "Database name or path"},
                    "sql": {"type": "string", "description": "SQL query (SELECT only)"},
                },
                "required": ["db", "sql"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> CallToolResult:
    try:
        if name == "oc_list_dbs":
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(_list_db_files(), ensure_ascii=False, default=str, indent=2))])

        elif name == "oc_list_sessions":
            db = arguments.get("db", "opencode.db")
            project_id = arguments.get("project_id")
            parent_id_filter = arguments.get("parent_id")
            search = arguments.get("search")
            limit = min(arguments.get("limit", 100), 5000)
            conn = _connect(db)
            c = conn.cursor()
            sql = """
                SELECT id, title, project_id, parent_id, directory, time_created, time_updated,
                       tokens_input, tokens_output, tokens_reasoning, cost
                FROM session WHERE 1=1
            """
            params: list = []
            if project_id:
                sql += " AND project_id = ?"
                params.append(project_id)
            if parent_id_filter == "null":
                sql += " AND parent_id IS NULL"
            elif parent_id_filter == "any":
                sql += " AND parent_id IS NOT NULL"
            elif parent_id_filter:
                sql += " AND parent_id = ?"
                params.append(parent_id_filter)
            if search:
                sql += " AND (title LIKE ? OR id LIKE ?)"
                params.extend([f"%{search}%", f"%{search}%"])
            sql += " ORDER BY time_updated DESC LIMIT ?"
            params.append(limit)
            c.execute(sql, params)
            rows = c.fetchall()
            columns = ["id", "title", "project_id", "parent_id", "directory", "time_created", "time_updated",
                       "tokens_input", "tokens_output", "tokens_reasoning", "cost"]
            result = [dict(zip(columns, r)) for r in rows]
            # Add child count
            for r in result:
                cnt = conn.execute("SELECT COUNT(*) FROM session WHERE parent_id = ?", (r["id"],)).fetchone()[0]
                r["children"] = cnt
            conn.close()
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str, indent=2))])

        elif name == "oc_get_session":
            db = arguments.get("db", "opencode.db")
            sid = arguments["session_id"]
            conn = _connect(db)
            c = conn.cursor()
            # Session metadata
            c.execute("""
                SELECT id, title, project_id, parent_id, slug, directory, path, version,
                       agent, model, time_created, time_updated, time_archived,
                       tokens_input, tokens_output, tokens_reasoning, tokens_cache_read, tokens_cache_write, cost, metadata
                FROM session WHERE id = ?
            """, (sid,))
            row = c.fetchone()
            if not row:
                conn.close()
                return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": "Session not found"}, ensure_ascii=False))])
            columns = ["id", "title", "project_id", "parent_id", "slug", "directory", "path", "version",
                       "agent", "model", "time_created", "time_updated", "time_archived",
                       "tokens_input", "tokens_output", "tokens_reasoning", "tokens_cache_read", "tokens_cache_write", "cost", "metadata"]
            session = dict(zip(columns, row))
            # Messages
            c.execute("SELECT id, time_created, data FROM message WHERE session_id = ? ORDER BY time_created", (sid,))
            messages = []
            for m in c.fetchall():
                msg = {"id": m[0], "time": m[1], "data": json.loads(m[2]) if m[2] else {}}
                # Parts for this message
                c.execute("SELECT id, data FROM part WHERE message_id = ? ORDER BY time_created", (m[0],))
                parts = []
                for p in c.fetchall():
                    pdata = json.loads(p[1]) if p[1] else {}
                    pdata["part_id"] = p[0]
                    parts.append(pdata)
                msg["parts"] = parts
                msg["part_count"] = len(parts)
                messages.append(msg)
            session["messages"] = messages
            session["message_count"] = len(messages)
            conn.close()
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(session, ensure_ascii=False, default=str, indent=2))])

        elif name == "oc_get_children":
            db = arguments.get("db", "opencode.db")
            pid = arguments["parent_id"]
            recursive = arguments.get("recursive", True)
            conn = _connect(db)
            # Get direct children
            c = conn.cursor()
            c.execute("""
                SELECT id, title, project_id, parent_id, directory, time_created, time_updated,
                       tokens_input, tokens_output, tokens_reasoning
                FROM session WHERE parent_id = ?
                ORDER BY time_created
            """, (pid,))
            columns = ["id", "title", "project_id", "parent_id", "directory", "time_created", "time_updated",
                       "tokens_input", "tokens_output", "tokens_reasoning"]
            result = [dict(zip(columns, r)) for r in c.fetchall()]
            # Recursively get nested children
            if recursive:
                def fetch_nested(parent_sid: str, depth: int = 0):
                    if depth > 10:
                        return []
                    c2 = conn.cursor()
                    c2.execute("SELECT id FROM session WHERE parent_id = ?", (parent_sid,))
                    nested = []
                    for (nid,) in c2.fetchall():
                        c2.execute("""
                            SELECT id, title, project_id, parent_id, directory, time_created, time_updated,
                                   tokens_input, tokens_output, tokens_reasoning
                            FROM session WHERE id = ?
                        """, (nid,))
                        r2 = c2.fetchone()
                        if r2:
                            item = dict(zip(columns, r2))
                            item["_depth"] = depth + 1
                            item["children_nested"] = fetch_nested(nid, depth + 1)
                            nested.append(item)
                    return nested

                for child in result:
                    child["children_nested"] = fetch_nested(child["id"])
            conn.close()
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str, indent=2))])

        elif name == "oc_check_orphans":
            return await _check_orphans()

        elif name == "oc_query":
            db = arguments.get("db", "opencode.db")
            sql = arguments["sql"].strip().upper()
            if not sql.startswith("SELECT") and "EXPLAIN" not in sql:
                return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": "Only SELECT queries allowed"}, ensure_ascii=False))])
            conn = _connect(db)
            c = conn.cursor()
            c.execute(arguments["sql"])
            columns = [desc[0] for desc in c.description] if c.description else []
            rows = c.fetchall()
            conn.close()
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"columns": columns, "rows": rows}, ensure_ascii=False, default=str, indent=2))])

        else:
            return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False))])

    except Exception as e:
        import traceback
        return CallToolResult(content=[TextContent(type="text", text=json.dumps({"error": str(e), "traceback": traceback.format_exc()}, ensure_ascii=False))])


async def _check_orphans() -> CallToolResult:
    """Compare opencode.db and opencode-dev.db."""
    dbs = _list_db_files()
    prod_path = str(BASE / "opencode.db")
    dev_path = str(BASE / "opencode-dev.db")
    result = {
        "databases_found": dbs,
        "comparison": {},
    }

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
        # Sessions with no parent (orphan children)
        orphans = c.execute("""
            SELECT COUNT(*) FROM session s1
            WHERE s1.parent_id IS NOT NULL
            AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)
        """).fetchone()[0]
        # Project distribution
        c.execute("SELECT project_id, COUNT(*) as cnt FROM session GROUP BY project_id ORDER BY cnt DESC")
        projects = {r[0]: r[1] for r in c.fetchall()}
        result["comparison"][label] = {
            "total": total,
            "root": roots,
            "children": children,
            "archived": archived,
            "orphan_children": orphans,
            "by_project": projects,
        }
        conn.close()

    # Compare session IDs between DBs
    if os.path.exists(prod_path) and os.path.exists(dev_path):
        conn_prod = sqlite3.connect(prod_path, timeout=2)
        conn_dev = sqlite3.connect(dev_path, timeout=2)
        prod_ids = set(r[0] for r in conn_prod.execute("SELECT id FROM session").fetchall())
        dev_ids = set(r[0] for r in conn_dev.execute("SELECT id FROM session").fetchall())
        result["cross_db"] = {
            "only_in_opencode_db": list(prod_ids - dev_ids),
            "only_in_opencode_dev_db": list(dev_ids - prod_ids),
            "in_both": len(prod_ids & dev_ids),
        }
        # For sessions in both: check project_id mismatch
        common = prod_ids & dev_ids
        mismatches = []
        for sid in common:
            p_pid = conn_prod.execute("SELECT project_id FROM session WHERE id = ?", (sid,)).fetchone()[0]
            d_pid = conn_dev.execute("SELECT project_id FROM session WHERE id = ?", (sid,)).fetchone()[0]
            if p_pid != d_pid:
                mismatches.append({"id": sid, "opencode.db": p_pid, "opencode-dev.db": d_pid})
        if mismatches:
            result["cross_db"]["project_id_mismatches"] = mismatches
        conn_prod.close()
        conn_dev.close()

    return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, ensure_ascii=False, default=str, indent=2))])


async def main():
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
