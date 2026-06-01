"""Core module for OpenCode Session Manager.
Works with OpenCode SQLite database and CLI for session management.
"""

import sqlite3
import os
import json
import shutil
import dataclasses
from pathlib import Path
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Optional


@dataclass
class SessionInfo:
    id: str
    title: str
    directory: str
    model: str
    time_created: int
    time_updated: int
    message_count: int = 0
    part_count: int = 0
    tokens_input: int = 0
    tokens_output: int = 0
    tokens_reasoning: int = 0
    cost: float = 0.0
    size_bytes: int = 0
    parent_id: Optional[str] = None
    is_subagent: bool = False

    @property
    def created_dt(self) -> datetime:
        return datetime.fromtimestamp(self.time_created / 1000, tz=timezone.utc)

    @property
    def updated_dt(self) -> datetime:
        return datetime.fromtimestamp(self.time_updated / 1000, tz=timezone.utc)

    @property
    def age_str(self) -> str:
        now = datetime.now(timezone.utc)
        delta = now - self.created_dt
        days = delta.days
        if days == 0:
            hours = delta.seconds // 3600
            return f"{hours}h ago" if hours > 0 else "today"
        elif days < 7:
            return f"{days}d ago"
        elif days < 30:
            return f"{days // 7}w ago"
        else:
            return f"{days // 30}mo ago"

    @property
    def size_str(self) -> str:
        mb = self.size_bytes / (1024 * 1024)
        if mb >= 1024:
            return f"{mb / 1024:.1f} GB"
        return f"{mb:.1f} MB"


@dataclass
class DBStats:
    db_size_bytes: int = 0
    wal_size_bytes: int = 0
    total_sessions: int = 0
    root_sessions: int = 0
    subagent_sessions: int = 0
    total_messages: int = 0
    total_parts: int = 0
    session_diff_size_bytes: int = 0
    session_diff_count: int = 0
    snapshot_size_bytes: int = 0
    snapshot_projects: int = 0


class OpenCodeDB:
    """Interface to OpenCode SQLite database."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            detected = self._detect_db_path()
            db_path = detected or os.path.expanduser(r"~\.local\share\opencode\opencode.db")
        self.db_path = db_path
        self._opencode_base = Path(db_path).parent

    @staticmethod
    def _detect_db_path() -> Optional[str]:
        """Detect active opencode database.

        Scans all opencode DBs and returns the one with the most sessions
        (typically opencode.db for stable installs). Falls back to CLI path
        or heuristic detection.
        """
        base = Path.home() / ".local" / "share" / "opencode"
        candidates: list[tuple[str, int]] = []  # (path, session_count)

        # Collect all potential DBs
        for f in base.glob("opencode*.db"):
            if f.name.endswith(".db-shm") or f.name.endswith(".db-wal"):
                continue
            try:
                conn = sqlite3.connect(str(f))
                c = conn.cursor()
                c.execute("SELECT COUNT(*) FROM session")
                count = c.fetchone()[0]
                conn.close()
                candidates.append((str(f), count))
            except Exception:
                candidates.append((str(f), 0))

        # Prefer DB with most sessions (user's real data)
        if candidates:
            candidates.sort(key=lambda x: (-x[1], x[0]))
            # Prefer opencode.db over copies when session count is tied
            best = candidates[0]
            for path, count in candidates:
                if count < best[1]:
                    break
                if os.path.basename(path).lower() == "opencode.db":
                    best = (path, count)
            return best[0]

        # Fallback: try 'opencode db path' via CLI
        try:
            import subprocess
            for cmd in [
                ["powershell", "-NoProfile", "-Command", "opencode db path"],
            ]:
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                if result.returncode == 0:
                    path = result.stdout.strip().split("\n")[-1].strip()
                    if path and os.path.exists(path):
                        return path
        except Exception:
            pass

        return None

    def connect(self, readonly: bool = True) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=10)

    def get_sessions(self, sort_by: str = "size", ascending: bool = False,
                      limit: Optional[int] = None) -> list[SessionInfo]:
        conn = self.connect(readonly=True)
        c = conn.cursor()

        # Step 1: Get session info + message counts (fast)
        msg_query = """
            SELECT session_id, COUNT(*) as cnt
            FROM message
            GROUP BY session_id
        """
        c.execute(msg_query)
        msg_counts = {row[0]: row[1] for row in c.fetchall()}

        # Step 2: Get size per session (sum of part data)
        size_query = """
            SELECT session_id, SUM(LENGTH(data)) as total_size
            FROM part
            GROUP BY session_id
        """
        c.execute(size_query)
        sizes = {row[0]: row[1] or 0 for row in c.fetchall()}

        # Step 3: Get session metadata
        sess_query = """
            SELECT id, title, directory, model, time_created, time_updated,
                   parent_id, tokens_input, tokens_output, tokens_reasoning, cost
            FROM session
        """

        # SQL-side sorting for columns that map directly to DB fields
        sql_sort_map = {
            "title": ("title", ascending),
            "age": ("time_created", ascending),
            "tokens_in": ("tokens_input", ascending),
            "tokens_out": ("tokens_output", ascending),
            "reasoning": ("tokens_reasoning", ascending),
        }

        if sort_by in sql_sort_map:
            col, asc = sql_sort_map[sort_by]
            direction = "ASC" if asc else "DESC"
            sess_query += f" ORDER BY {col} {direction}"

        c.execute(sess_query)
        rows = c.fetchall()
        conn.close()

        sessions = []
        for r in rows:
            sid = r[0]
            sessions.append(SessionInfo(
                id=sid,
                title=r[1] or "Untitled",
                directory=r[2] or "",
                model=r[3] or "",
                time_created=r[4],
                time_updated=r[5],
                parent_id=r[6],
                tokens_input=r[7] or 0,
                tokens_output=r[8] or 0,
                tokens_reasoning=r[9] or 0,
                cost=r[10] or 0.0,
                message_count=msg_counts.get(sid, 0),
                size_bytes=sizes.get(sid, 0),
                is_subagent=r[6] is not None,
            ))

        # Python-side sorting for computed columns
        if sort_by == "size":
            sessions.sort(key=lambda s: s.size_bytes, reverse=not ascending)
        elif sort_by == "messages":
            sessions.sort(key=lambda s: s.message_count, reverse=not ascending)
        elif sort_by == "model":
            sessions.sort(key=lambda s: s.model.lower(), reverse=not ascending)

        if limit:
            sessions = sessions[:limit]

        return sessions

    def get_session(self, session_id: str) -> Optional[SessionInfo]:
        conn = self.connect(readonly=True)
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM message WHERE session_id = ?", (session_id,))
        msg_count = c.fetchone()[0]

        c.execute("SELECT COALESCE(SUM(LENGTH(data)), 0) FROM part WHERE session_id = ?", (session_id,))
        size_bytes = c.fetchone()[0]

        c.execute("""
            SELECT id, title, directory, model, time_created, time_updated,
                   parent_id, tokens_input, tokens_output, tokens_reasoning, cost
            FROM session WHERE id = ?
        """, (session_id,))
        r = c.fetchone()
        conn.close()

        if not r:
            return None

        return SessionInfo(
            id=r[0],
            title=r[1] or "Untitled",
            directory=r[2] or "",
            model=r[3] or "",
            time_created=r[4],
            time_updated=r[5],
            parent_id=r[6],
            tokens_input=r[7] or 0,
            tokens_output=r[8] or 0,
            tokens_reasoning=r[9] or 0,
            cost=r[10] or 0.0,
            message_count=msg_count,
            size_bytes=size_bytes,
            is_subagent=r[6] is not None,
        )

    def get_stats(self) -> DBStats:
        stats = DBStats()

        # DB file size
        if os.path.exists(self.db_path):
            stats.db_size_bytes = os.path.getsize(self.db_path)
        wal_path = self.db_path + "-wal"
        if os.path.exists(wal_path):
            stats.wal_size_bytes = os.path.getsize(wal_path)

        # Session counts
        conn = self.connect(readonly=True)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM session")
        stats.total_sessions = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM session WHERE parent_id IS NOT NULL")
        stats.subagent_sessions = c.fetchone()[0]
        stats.root_sessions = stats.total_sessions - stats.subagent_sessions

        c.execute("SELECT COUNT(*) FROM message")
        stats.total_messages = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM part")
        stats.total_parts = c.fetchone()[0]
        conn.close()

        # Session diffs
        diff_dir = self._opencode_base / "storage" / "session_diff"
        if diff_dir.exists():
            diff_files = list(diff_dir.glob("*.json"))
            stats.session_diff_count = len(diff_files)
            stats.session_diff_size_bytes = sum(f.stat().st_size for f in diff_files)

        # Snapshots
        snap_dir = self._opencode_base / "snapshot"
        if snap_dir.exists():
            projects = [d for d in snap_dir.iterdir() if d.is_dir()]
            stats.snapshot_projects = len(projects)
            stats.snapshot_size_bytes = sum(
                sum(f.stat().st_size for f in p.rglob("*") if f.is_file())
                for p in projects
            )

        return stats

    def delete_session(self, session_id: str) -> bool:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            # Delete parts
            c.execute("DELETE FROM part WHERE session_id = ?", (session_id,))
            # Delete messages
            c.execute("DELETE FROM message WHERE session_id = ?", (session_id,))
            # Delete todos
            c.execute("DELETE FROM todo WHERE session_id = ?", (session_id,))
            # Delete session_share
            c.execute("DELETE FROM session_share WHERE session_id = ?", (session_id,))
            # Delete session
            c.execute("DELETE FROM session WHERE id = ?", (session_id,))
            conn.commit()

            # Also delete session_diff file
            diff_file = self._opencode_base / "storage" / "session_diff" / f"{session_id}.json"
            if diff_file.exists():
                diff_file.unlink()

            return True
        except Exception as e:
            conn.rollback()
            print(f"Error deleting session {session_id}: {e}")
            return False
        finally:
            conn.close()

    def _resolve_project_id(self, directory: str) -> str:
        """Resolve project_id from directory path, matching against project table."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "-C", directory, "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5
            )
            if result.returncode != 0:
                return "global"
            git_root = result.stdout.strip().replace("/", "\\")

            conn = self.connect(readonly=True)
            c = conn.cursor()
            c.execute("SELECT id, worktree FROM project WHERE id != 'global'")
            for row in c.fetchall():
                pid, wt = row[0], (row[1] or "").replace("/", "\\").rstrip("\\")
                if wt and (git_root == wt or git_root.startswith(wt + "\\")):
                    conn.close()
                    return pid
            conn.close()
            return "global"
        except Exception:
            return "global"

    def update_session_directory(self, session_id: str, new_directory: str) -> bool:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            # Normalize directory separators (tkinter may return forward slashes on Windows)
            if os.name == 'nt':
                new_directory = new_directory.replace('/', '\\')
            new_project_id = self._resolve_project_id(new_directory)
            new_path = new_directory.replace("\\", "/")

            c.execute("""
                UPDATE session
                SET directory = ?, path = ?, project_id = ?
                WHERE id = ?
            """, (new_directory, new_path, new_project_id, session_id))
            updated = c.rowcount

            # Also update all child (subagent) sessions
            c.execute("""
                UPDATE session
                SET directory = ?, path = ?, project_id = ?
                WHERE parent_id = ?
            """, (new_directory, new_path, new_project_id, session_id))

            conn.commit()
            return updated > 0
        except Exception as e:
            conn.rollback()
            print(f"Error updating directory: {e}")
            return False
        finally:
            conn.close()

    def strip_reasoning(self, session_id: Optional[str] = None) -> int:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            if session_id:
                c.execute("""
                    DELETE FROM part
                    WHERE session_id = ?
                    AND json_extract(data, '$.type') = 'reasoning'
                """, (session_id,))
            else:
                c.execute("""
                    DELETE FROM part
                    WHERE json_extract(data, '$.type') = 'reasoning'
                """)
            deleted = c.rowcount
            conn.commit()
            if deleted:
                if session_id:
                    self._update_session_counters(session_id, conn)
                else:
                    # Обновить все сессии
                    c2 = conn.cursor()
                    c2.execute("SELECT id FROM session")
                    for row in c2.fetchall():
                        self._update_session_counters(row[0], conn)
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"Error stripping reasoning: {e}")
            return 0
        finally:
            conn.close()

    def vacuum(self) -> bool:
        conn = self.connect(readonly=False)
        try:
            conn.execute("VACUUM")
            return True
        except Exception as e:
            print(f"Error vacuuming: {e}")
            return False
        finally:
            conn.close()

    def clean_snapshots(self) -> int:
        snap_dir = self._opencode_base / "snapshot"
        if not snap_dir.exists():
            return 0
        count = 0
        for d in snap_dir.iterdir():
            if d.is_dir() and d.name != "global":
                shutil.rmtree(d)
                count += 1
        return count

    def clean_orphan_diffs(self) -> int:
        diff_dir = self._opencode_base / "storage" / "session_diff"
        if not diff_dir.exists():
            return 0

        conn = self.connect(readonly=True)
        c = conn.cursor()
        c.execute("SELECT id FROM session")
        valid_ids = {row[0] for row in c.fetchall()}
        conn.close()

        count = 0
        for f in diff_dir.glob("*.json"):
            session_id = f.stem
            if session_id not in valid_ids:
                f.unlink()
                count += 1
        return count


    # === Message-level operations ===

    def get_chat_messages(self, session_id: str, limit: int = 500, offset: int = 0,
                           ascending: bool = True,
                           has_parts_only: bool = False) -> list[dict]:
        """Получить объединённый список сообщений и частей для chat-просмотра."""
        conn = self.connect(readonly=True)
        c = conn.cursor()
        order = "ASC" if ascending else "DESC"

        # Один запрос: все сообщения сессии, количество parts
        if has_parts_only:
            c.execute(f"""
                SELECT m.id, m.time_created, m.data,
                       COALESCE(pc.part_count, 0) as part_count
                FROM message m
                INNER JOIN (
                    SELECT message_id, COUNT(*) as part_count
                    FROM part
                    WHERE session_id = ?
                    GROUP BY message_id
                ) pc ON pc.message_id = m.id
                ORDER BY m.time_created {order}
                LIMIT ? OFFSET ?
            """, (session_id, limit, offset))
        else:
            c.execute(f"""
                SELECT m.id, m.time_created, m.data,
                       COALESCE(pc.part_count, 0) as part_count
                FROM message m
                LEFT JOIN (
                    SELECT message_id, COUNT(*) as part_count
                    FROM part
                    WHERE session_id = ?
                    GROUP BY message_id
                ) pc ON pc.message_id = m.id
                WHERE m.session_id = ?
                ORDER BY m.time_created {order}
                LIMIT ? OFFSET ?
            """, (session_id, session_id, limit, offset))

        rows = c.fetchall()

        messages = []
        for row in rows:
            msg_id = row[0]
            msg_time = row[1]
            msg_data = json.loads(row[2]) if row[2] else {}
            role = msg_data.get("role", "unknown")

            # Получить parts этого сообщения
            c.execute("""
                SELECT id, data FROM part
                WHERE message_id = ?
                ORDER BY time_created
            """, (msg_id,))

            parts = []
            for prow in c.fetchall():
                pdata = json.loads(prow[1]) if prow[1] else {}
                parts.append({
                    "id": prow[0],
                    "type": pdata.get("type", "unknown"),
                    "text": pdata.get("text", ""),
                    "data": pdata,
                    "size": len(prow[1]) if prow[1] else 0,
                })

            messages.append({
                "id": msg_id,
                "time": msg_time,
                "role": role,
                "parts": parts,
                "data": msg_data,
            })

        conn.close()
        return messages

    def get_chat_messages_count(self, session_id: str, has_parts_only: bool = False) -> int:
        conn = self.connect(readonly=True)
        c = conn.cursor()
        if has_parts_only:
            c.execute("""
                SELECT COUNT(*) FROM message m
                WHERE EXISTS (SELECT 1 FROM part WHERE message_id = m.id)
                AND m.session_id = ?
            """, (session_id,))
        else:
            c.execute("SELECT COUNT(*) FROM message WHERE session_id = ?", (session_id,))
        count = c.fetchone()[0]
        conn.close()
        return count

    def get_parts(self, session_id: str, limit: int = 5000, offset: int = 0, ascending: bool = True) -> list[dict]:
        conn = self.connect(readonly=True)
        c = conn.cursor()
        order = "ASC" if ascending else "DESC"
        c.execute(f"""
            SELECT p.id, p.message_id, p.time_created, p.data
            FROM part p
            WHERE p.session_id = ?
            ORDER BY p.time_created {order}
            LIMIT ? OFFSET ?
        """, (session_id, limit, offset))
        parts = []
        for row in c.fetchall():
            data = json.loads(row[3]) if row[3] else {}
            ptype = data.get("type", "unknown")
            state = data.get("state", {})
            text = data.get("text", "")
            size = len(row[3]) if row[3] else 0
            parts.append({
                "id": row[0],
                "message_id": row[1],
                "time_created": row[2],
                "type": ptype,
                "text": text[:500] if text else "",
                "status": state.get("status", ""),
                "size": size,
                "data": data,
            })
        conn.close()
        return parts

    def get_parts_count(self, session_id: str) -> int:
        conn = self.connect(readonly=True)
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM part WHERE session_id = ?", (session_id,))
        count = c.fetchone()[0]
        conn.close()
        return count

    def get_parts_summary(self, session_id: str) -> dict:
        conn = self.connect(readonly=True)
        c = conn.cursor()
        c.execute("""
            SELECT
                json_extract(data, '$.type') as ptype,
                COUNT(*) as cnt,
                SUM(LENGTH(data)) as total_size
            FROM part
            WHERE session_id = ?
            GROUP BY ptype
        """, (session_id,))
        summary = {}
        for row in c.fetchall():
            summary[row[0] or "unknown"] = {
                "count": row[1],
                "size": row[2] or 0,
            }
        conn.close()
        return summary

    def delete_message(self, message_id: str) -> bool:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            c.execute("DELETE FROM part WHERE message_id = ?", (message_id,))
            c.execute("DELETE FROM message WHERE id = ?", (message_id,))
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            print(f"Error deleting message {message_id}: {e}")
            return False
        finally:
            conn.close()

    def delete_messages(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        conn = self.connect(readonly=False)
        c = conn.cursor()
        deleted = 0
        try:
            for mid in message_ids:
                c.execute("DELETE FROM part WHERE message_id = ?", (mid,))
                c.execute("DELETE FROM message WHERE id = ?", (mid,))
                deleted += 1
            conn.commit()
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"Error deleting messages: {e}")
            return 0
        finally:
            conn.close()

    def delete_parts_by_type(self, session_id: str, ptype: str) -> int:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            c.execute("""
                DELETE FROM part
                WHERE session_id = ?
                AND json_extract(data, '$.type') = ?
            """, (session_id, ptype))
            deleted = c.rowcount
            conn.commit()
            if deleted:
                self._update_session_counters(session_id, conn)
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"Error deleting parts: {e}")
            return 0
        finally:
            conn.close()

    def delete_parts_by_status(self, session_id: str, status: str) -> int:
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            c.execute("""
                DELETE FROM part
                WHERE session_id = ?
                AND json_extract(data, '$.state.status') = ?
            """, (session_id, status))
            deleted = c.rowcount
            conn.commit()
            if deleted:
                self._update_session_counters(session_id, conn)
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"Error deleting parts by status: {e}")
            return 0
        finally:
            conn.close()

    def delete_old_messages(self, session_id: str, max_age_days: int = 7) -> int:
        import time
        cutoff = int((time.time() - max_age_days * 86400) * 1000)
        conn = self.connect(readonly=False)
        c = conn.cursor()
        try:
            c.execute("SELECT id FROM message WHERE session_id = ? AND time_created < ?", (session_id, cutoff))
            old_ids = [row[0] for row in c.fetchall()]
            for mid in old_ids:
                c.execute("DELETE FROM part WHERE message_id = ?", (mid,))
            c.execute("DELETE FROM message WHERE session_id = ? AND time_created < ?", (session_id, cutoff))
            deleted = c.rowcount
            conn.commit()
            if deleted:
                self._update_session_counters(session_id, conn)
            return deleted
        except Exception as e:
            conn.rollback()
            print(f"Error deleting old messages: {e}")
            return 0
        finally:
            conn.close()

    def _update_session_counters(self, session_id: str, conn: sqlite3.Connection = None):
        """Пересчитать счётчики tokens и message_count в session по оставшимся parts."""
        own_conn = False
        if conn is None:
            conn = self.connect(readonly=False)
            own_conn = True
        c = conn.cursor()
        try:
            # Удалить message без parts
            c.execute("""
                DELETE FROM message
                WHERE session_id = ?
                AND id NOT IN (SELECT DISTINCT message_id FROM part WHERE session_id = ?)
            """, (session_id, session_id))
            orphaned = c.rowcount

            # Подсчитать реальные токены из оставшихся parts
            c.execute("""
                SELECT
                    SUM(CASE WHEN json_extract(data, '$.type') = 'text' THEN LENGTH(json_extract(data, '$.text')) ELSE 0 END) as text_chars,
                    SUM(CASE WHEN json_extract(data, '$.type') = 'tool' THEN LENGTH(CAST(data as TEXT)) ELSE 0 END) as tool_chars,
                    SUM(CASE WHEN json_extract(data, '$.type') = 'reasoning' THEN LENGTH(json_extract(data, '$.text')) ELSE 0 END) as reasoning_chars,
                    SUM(CASE WHEN json_extract(data, '$.type') = 'patch' THEN LENGTH(CAST(data as TEXT)) ELSE 0 END) as patch_chars
                FROM part WHERE session_id = ?
            """, (session_id,))
            row = c.fetchone()
            text_chars = row[0] or 0
            tool_chars = row[1] or 0
            reasoning_chars = row[2] or 0
            patch_chars = row[3] or 0

            # Грубая оценка: ~4 символа на токен
            tokens_input = (text_chars + tool_chars + patch_chars) // 4
            tokens_output = text_chars // 4
            tokens_reasoning = reasoning_chars // 4

            # Подсчитать количество сообщений
            c.execute("SELECT COUNT(*) FROM message WHERE session_id = ?", (session_id,))
            msg_count = c.fetchone()[0]

            c.execute("""
                UPDATE session
                SET tokens_input = ?, tokens_output = ?, tokens_reasoning = ?
                WHERE id = ?
            """, (tokens_input, tokens_output, tokens_reasoning, session_id))

            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"Error updating session counters: {e}")
        finally:
            if own_conn:
                conn.close()


class OpenCodeCLI:
    """Interface to OpenCode database for export/import.
    
    Uses OpenCodeDB (SQLite) directly instead of CLI commands,
    since 'opencode export/import/session list' commands do not exist.
    """

    def __init__(self, opencode_cmd: str = "opencode"):
        self.db = OpenCodeDB()

    def export_session(self, session_id: str, output_path: str) -> bool:
        """Export session to JSON by reading directly from SQLite."""
        try:
            session = self.db.get_session(session_id)
            if not session:
                print(f"Session {session_id} not found")
                return False

            messages = self.db.get_chat_messages(session_id, limit=10000, ascending=True)
            parts = self.db.get_parts(session_id, limit=50000, ascending=True)

            data = {
                "session": dataclasses.asdict(session),
                "messages": messages,
                "parts": parts,
                "exported_at": datetime.now(timezone.utc).isoformat(),
                "version": "1.0",
            }

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=str)
            return True

        except Exception as e:
            print(f"Export error: {e}")
            return False

    def import_session(self, file_path: str) -> bool:
        """Import session from JSON. Creates backup before writing to DB."""
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            sess = data.get("session")
            if not sess or not sess.get("id"):
                print("Import error: missing session.id in JSON")
                return False

            session_id = sess["id"]

            # Backup DB
            db_path = self.db.db_path
            backup_path = f"{db_path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            shutil.copy2(db_path, backup_path)
            print(f"Backup created: {backup_path}")

            conn = self.db.connect(readonly=False)
            c = conn.cursor()

            # Clear existing messages/parts for this session before import
            c.execute("DELETE FROM part WHERE session_id = ?", (session_id,))
            c.execute("DELETE FROM message WHERE session_id = ?", (session_id,))

            # Generate required fields if missing
            import random
            slug = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=12))
            version = sess.get("version", "local")
            project_id = sess.get("project_id", "global")
            model_json = sess.get("model", "")
            if isinstance(model_json, dict):
                model_json = json.dumps(model_json, ensure_ascii=False)

            # Insert session with all required columns
            c.execute("""INSERT OR REPLACE INTO session
                (id, project_id, slug, directory, title, version,
                 model, time_created, time_updated, parent_id,
                 tokens_input, tokens_output, tokens_reasoning,
                 tokens_cache_read, tokens_cache_write, cost)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, project_id, slug,
                 sess.get("directory", ""), sess.get("title", "Untitled"), version,
                 model_json, sess.get("time_created", 0),
                 sess.get("time_updated", 0), sess.get("parent_id"),
                 sess.get("tokens_input", 0), sess.get("tokens_output", 0),
                 sess.get("tokens_reasoning", 0),
                 0, 0, sess.get("cost", 0.0)))

            # Insert messages with their parts
            for msg in data.get("messages", []):
                msg_time = msg.get("time", 0)
                c.execute("""INSERT OR REPLACE INTO message
                    (id, session_id, time_created, time_updated, data)
                    VALUES (?, ?, ?, ?, ?)""",
                    (msg["id"], session_id, msg_time, msg_time,
                     json.dumps(msg.get("data", {}), ensure_ascii=False)))

                for part in msg.get("parts", []):
                    part_time = part.get("time_created") or msg_time
                    part_data = part.get("data", {})
                    if not part_data:
                        # Reconstruct data from flattened fields
                        part_data = {"type": part.get("type", "unknown")}
                        if part.get("text"):
                            part_data["text"] = part["text"]
                        if part.get("status"):
                            part_data.setdefault("state", {})
                            part_data["state"]["status"] = part["status"]
                    c.execute("""INSERT OR REPLACE INTO part
                        (id, message_id, session_id, time_created, time_updated, data)
                        VALUES (?, ?, ?, ?, ?, ?)""",
                        (part["id"], msg["id"], session_id,
                         part_time, part_time,
                         json.dumps(part_data, ensure_ascii=False)))

            # Also insert standalone parts (not nested under messages)
            for part in data.get("parts", []):
                part_time = part.get("time_created", 0)
                part_data = part.get("data", {})
                if not part_data:
                    part_data = {"type": part.get("type", "unknown")}
                    if part.get("text"):
                        part_data["text"] = part["text"]
                    if part.get("status"):
                        part_data.setdefault("state", {})
                        part_data["state"]["status"] = part["status"]
                c.execute("""INSERT OR REPLACE INTO part
                    (id, message_id, session_id, time_created, time_updated, data)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (part["id"], part.get("message_id", ""), session_id,
                     part_time, part_time,
                     json.dumps(part_data, ensure_ascii=False)))

            conn.commit()
            conn.close()
            print(f"Import successful for session {session_id}")
            print(f"Backup saved: {backup_path}")
            print("IMPORTANT: Verify opencode works correctly after import.")
            return True

        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"Import error: {e}")
            return False
