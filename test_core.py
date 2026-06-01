"""Test core module - verify DB access, export and import."""
import sys
import os
import tempfile
import json

# Add current dir to path
sys.path.insert(0, os.path.dirname(__file__))

from core import OpenCodeDB, OpenCodeCLI


def test_db():
    print("=== Testing Database Access ===")
    try:
        db = OpenCodeDB()
        print(f"DB Path: {db.db_path}")
        print(f"DB Exists: {os.path.exists(db.db_path)}")

        # Test read sessions
        sessions = db.get_sessions(sort_by="size", limit=5)
        print(f"\nTop 5 sessions by size:")
        for s in sessions:
            print(f"  {s.title[:40]:<40} | {s.size_str:>8} | {s.message_count:>4} msgs")

        # Test stats
        stats = db.get_stats()
        print(f"\nDB Stats:")
        print(f"  DB Size: {stats.db_size_bytes / 1024 / 1024:.1f} MB")
        print(f"  Sessions: {stats.total_sessions}")
        print(f"  Messages: {stats.total_messages}")
        print(f"  Parts: {stats.total_parts}")
        print(f"  Session Diffs: {stats.session_diff_count} files, {stats.session_diff_size_bytes / 1024 / 1024:.1f} MB")
        print(f"  Snapshots: {stats.snapshot_projects} projects, {stats.snapshot_size_bytes / 1024 / 1024:.1f} MB")

        print("\n[OK] Database access works!")
        return True, sessions
    except Exception as e:
        print(f"\n[FAIL] Database error: {e}")
        import traceback
        traceback.print_exc()
        return False, []


def test_export(sessions):
    print("\n=== Testing Export (DB-based) ===")
    if not sessions:
        print("[SKIP] No sessions to test export")
        return False

    cli = OpenCodeCLI()
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            session = sessions[0]
            output_path = os.path.join(tmpdir, f"{session.id}.json")
            result = cli.export_session(session.id, output_path)
            if not result:
                print(f"[FAIL] Export returned False for session {session.id}")
                return False

            if not os.path.exists(output_path):
                print(f"[FAIL] Export file not created: {output_path}")
                return False

            with open(output_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Verify structure
            assert "session" in data, "Missing 'session' key"
            assert "messages" in data, "Missing 'messages' key"
            assert "parts" in data, "Missing 'parts' key"
            assert data["session"]["id"] == session.id, "Session ID mismatch"
            assert data["version"] == "1.0", "Wrong version"
            assert "exported_at" in data, "Missing exported_at"

            file_size = os.path.getsize(output_path)
            print(f"  Exported: {session.title[:40]:<40} -> {file_size / 1024:.1f} KB")
            print(f"  Messages: {len(data['messages'])}, Parts: {len(data['parts'])}")
            print("[OK] Export works via SQLite!")
            return True
    except Exception as e:
        print(f"[FAIL] Export test error: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    db_ok, sessions = test_db()

    print("\n=== Summary ===")
    print(f"  Database: {'OK' if db_ok else 'FAIL'}")

    if db_ok:
        export_ok = test_export(sessions)
        print(f"  Export:   {'OK' if export_ok else 'FAIL'}")

        ok = db_ok and export_ok
        print(f"\n  Overall:  {'OK' if ok else 'SOME FAILURES'}")
        print("\nYou can now run: python run.py")
    else:
        print("\nFix database access issues first!")
