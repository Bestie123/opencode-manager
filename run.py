#!/usr/bin/env python3
"""OpenCode Session Manager - Launcher."""
import sys
import subprocess
import importlib

def check_deps():
    """Check if all dependencies are available."""
    missing = []
    # tkinter is built-in, no need to check
    # core.py uses only stdlib: sqlite3, os, json, subprocess, shutil, pathlib, datetime, dataclasses
    return missing

def main():
    missing = check_deps()
    if missing:
        print("Missing dependencies:")
        for m in missing:
            print(f"  - {m}")
        print("\nInstall with: pip install -r requirements.txt")
        sys.exit(1)

    from app import main as app_main
    app_main()

if __name__ == "__main__":
    main()
