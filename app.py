"""OpenCode Session Manager - GUI приложение.
Tkinter-интерфейс для управления сессиями OpenCode.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import sys
import threading
import os
import json
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from core import OpenCodeDB, OpenCodeCLI, SessionInfo, DBStats


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("OpenCode Session Manager")
        self.geometry("1200x750")
        self.minsize(950, 550)

        self.db = OpenCodeDB()
        self.cli = OpenCodeCLI()
        self._db_list = []

        self._config_path = Path.home() / ".opencode-manager" / "config.json"
        self._dark_mode = False
        self._load_config()

        self._setup_styles()
        self._create_menu()
        self._create_main_layout()
        self._create_status_bar()

        # Apply saved theme: invert then toggle flips back
        self._dark_mode = not self._dark_mode
        self._toggle_theme()

        self.after(100, self._load_sessions)

    def _load_config(self):
        try:
            if self._config_path.exists():
                cfg = json.loads(self._config_path.read_text(encoding="utf-8"))
                self._dark_mode = cfg.get("dark_mode", True)
        except Exception:
            self._dark_mode = True

    def _save_config(self):
        try:
            self._config_path.parent.mkdir(parents=True, exist_ok=True)
            self._config_path.write_text(
                json.dumps({"dark_mode": self._dark_mode}, indent=2),
                encoding="utf-8"
            )
        except Exception:
            pass

    def _setup_styles(self):
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("Treeview", rowheight=24, font=("Consolas", 9))
        style.configure("Treeview.Heading", font=("Segoe UI", 9, "bold"))
        style.configure("TButton", padding=4)
        style.configure("TLabel", padding=2)
        style.configure("Header.TLabel", font=("Segoe UI", 11, "bold"))
        style.configure("Stat.TLabel", font=("Consolas", 10))
        style.configure("Danger.TButton", foreground="red")

    def _create_menu(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="Обновить (F5)", command=self._load_sessions, accelerator="F5")
        file_menu.add_separator()
        file_menu.add_command(label="Выход", command=self.quit)
        menubar.add_cascade(label="Файл", menu=file_menu)

        tools_menu = tk.Menu(menubar, tearoff=0)
        tools_menu.add_command(label="Vacuum БД", command=self._vacuum_db)
        tools_menu.add_command(label="Очистить снапшоты", command=self._clean_snapshots)
        tools_menu.add_command(label="Очистить осиротевшие diff'ы", command=self._clean_orphans)
        tools_menu.add_separator()
        tools_menu.add_command(label="Удалить reasoning (все)", command=self._strip_all_reasoning)
        tools_menu.add_separator()
        self._theme_label = tk.StringVar(value="Тёмная тема")
        tools_menu.add_command(label="Переключить тему", command=self._toggle_theme)
        menubar.add_cascade(label="Инструменты", menu=tools_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="О программе", command=self._show_about)
        menubar.add_cascade(label="Справка", menu=help_menu)

        self.bind("<F5>", lambda e: self._load_sessions())

    def _create_main_layout(self):
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._create_sessions_tab()
        self._create_messages_tab()
        self._create_cleanup_tab()
        self._create_dashboard_tab()
        self._create_help_tab()

        self._need_sessions_refresh = False
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    # ─────────────────────────────────────────────
    # ВКЛАДКА: СЕССИИ
    # ─────────────────────────────────────────────
    def _create_sessions_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Сессии  ")

        # Toolbar
        toolbar = ttk.Frame(frame)
        toolbar.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(toolbar, text="БД:").pack(side=tk.LEFT, padx=(0, 3))
        self.db_var = tk.StringVar()
        self.db_combo = ttk.Combobox(toolbar, textvariable=self.db_var,
                                      width=22, state="readonly")
        self.db_combo.pack(side=tk.LEFT, padx=(0, 10))
        self.db_combo.bind("<<ComboboxSelected>>", self._on_db_selected)
        self._refresh_db_list()

        ttk.Label(toolbar, text="Сортировка:").pack(side=tk.LEFT, padx=(0, 5))
        self.sort_var = tk.StringVar(value="size")
        self.sort_labels = {"size": "По размеру", "age": "По дате", "name": "По имени"}
        self.sort_values = ["size", "age", "name"]
        sort_combo = ttk.Combobox(toolbar, textvariable=self.sort_var,
                                   values=self.sort_values, width=12, state="readonly")
        sort_combo.pack(side=tk.LEFT, padx=(0, 10))
        sort_combo.bind("<<ComboboxSelected>>", lambda e: self._load_sessions())

        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", self._filter_sessions)
        search_entry = ttk.Entry(toolbar, textvariable=self.search_var, width=30)
        search_entry.pack(side=tk.LEFT, padx=(0, 10))
        ttk.Label(toolbar, text="Поиск:").pack(side=tk.LEFT)

        self.subagent_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="Sub", variable=self.subagent_var,
                        command=self._refresh_tree).pack(side=tk.LEFT, padx=(0, 5))
        ttk.Button(toolbar, text="Обновить", command=self._load_sessions).pack(side=tk.RIGHT, padx=2)

        # Actions bar
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, padx=5, pady=(0, 5))

        ttk.Button(actions, text="Экспорт выбранных", command=self._export_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Экспорт всех", command=self._export_all).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Импорт JSON", command=self._import_session).pack(side=tk.LEFT, padx=2)
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(actions, text="Удалить выбранные", command=self._delete_selected,
                  style="Danger.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Strip reasoning", command=self._strip_selected_reasoning).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Открыть сообщения", command=self._open_messages_for_selected).pack(side=tk.LEFT, padx=2)
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        self._move_btn = ttk.Button(actions, text="Перенести в проект",
                                     command=self._change_session_directory)
        self._move_btn.pack(side=tk.LEFT, padx=2)
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        self._archive_btn = ttk.Button(actions, text="Архивировать",
                                       command=self._archive_selected)
        self._archive_btn.pack(side=tk.LEFT, padx=2)
        self._unarchive_btn = ttk.Button(actions, text="Разархивировать",
                                         command=self._unarchive_selected)
        self._unarchive_btn.pack(side=tk.LEFT, padx=2)

        self.selected_label = ttk.Label(actions, text="Выбрано: 0")
        self.selected_label.pack(side=tk.RIGHT, padx=5)

        # Treeview — иерархический (родитель-дочерние)
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        columns = ("children", "status", "directory", "size", "messages", "tokens_in", "tokens_out", "reasoning", "age", "model")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="tree headings", selectmode="extended")

        self.tree.heading("#0", text="Название", command=lambda: self._sort_sessions("title"))
        self.tree.heading("children", text="Дочер.", command=lambda: self._sort_sessions("children"))
        self.tree.heading("status", text="Сост.", command=lambda: self._sort_sessions("status"))
        self.tree.heading("directory", text="Директория", command=lambda: self._sort_sessions("directory"))
        self.tree.heading("size", text="Размер", command=lambda: self._sort_sessions("size"))
        self.tree.heading("messages", text="Сообщ.", command=lambda: self._sort_sessions("messages"))
        self.tree.heading("tokens_in", text="Tokens In", command=lambda: self._sort_sessions("tokens_in"))
        self.tree.heading("tokens_out", text="Tokens Out", command=lambda: self._sort_sessions("tokens_out"))
        self.tree.heading("reasoning", text="Reasoning", command=lambda: self._sort_sessions("reasoning"))
        self.tree.heading("age", text="Возраст", command=lambda: self._sort_sessions("age"))
        self.tree.heading("model", text="Модель", command=lambda: self._sort_sessions("model"))

        self.tree.column("#0", width=280, minwidth=130)
        self.tree.column("children", width=55, minwidth=40, stretch=False)
        self.tree.column("status", width=55, minwidth=40, stretch=False)
        self.tree.column("directory", width=260, minwidth=110)
        self.tree.column("size", width=80, minwidth=60, stretch=False)
        self.tree.column("messages", width=70, minwidth=40, stretch=False)
        self.tree.column("tokens_in", width=90, minwidth=60, stretch=False)
        self.tree.column("tokens_out", width=90, minwidth=60, stretch=False)
        self.tree.column("reasoning", width=90, minwidth=60, stretch=False)
        self.tree.column("age", width=70, minwidth=50, stretch=False)
        self.tree.column("model", width=120, minwidth=80)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.tree.tag_configure("subagent", foreground="#8b949e", font=("Consolas", 9, "italic"))

        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-1>", self._on_tree_double_click)

        self._sessions = []
        self._session_map = {}  # iid -> SessionInfo
        self._all_sessions = []
        self._session_sort_col = "size"
        self._session_sort_asc = False

    def _on_tree_select(self, event):
        sel = self.tree.selection()
        self.selected_label.config(text=f"Выбрано: {len(sel)}")
        # Disable "move to project" for subagent sessions
        has_subagent = False
        any_archived = False
        any_active = False
        for iid in sel:
            s = self._session_map.get(iid)
            if s:
                if s.parent_id:
                    has_subagent = True
                if s.is_archived:
                    any_archived = True
                else:
                    any_active = True
        self._move_btn.config(state=tk.DISABLED if has_subagent else tk.NORMAL)
        self._archive_btn.config(state=tk.DISABLED if not any_active else tk.NORMAL)
        self._unarchive_btn.config(state=tk.DISABLED if not any_archived else tk.NORMAL)

    def _on_tree_double_click(self, event):
        sel = self.tree.selection()
        if sel:
            session = self._session_map.get(sel[0])
            if session:
                self._open_messages_for_session(session.id)

    def _refresh_db_list(self):
        self._db_list = OpenCodeDB.list_databases()
        labels = [f"{label} ({count} сес.)" for path, label, count in self._db_list]
        self.db_combo["values"] = labels
        if self._db_list:
            # Select current DB
            current = self.db.db_path
            for i, (path, label, count) in enumerate(self._db_list):
                if path == current:
                    self.db_combo.current(i)
                    break
            else:
                self.db_combo.current(0)
                self._switch_db(0)

    def _on_db_selected(self, event=None):
        idx = self.db_combo.current()
        if idx >= 0:
            self._switch_db(idx)

    def _switch_db(self, idx: int):
        if idx < 0 or idx >= len(self._db_list):
            return
        path, label, _ = self._db_list[idx]
        if path == self.db.db_path:
            return
        self.db = OpenCodeDB(path)
        self.cli = OpenCodeCLI(db_path=path)
        self._load_sessions()
        self.status_var.set(f"БД: {label} ({path})")

    def _sort_sessions(self, col):
        if self._session_sort_col == col:
            self._session_sort_asc = not self._session_sort_asc
        else:
            self._session_sort_col = col
            self._session_sort_asc = True

        # Update header arrows (column #0 = title, rest are named columns)
        cols = ("title", "children", "status", "directory", "size", "messages", "tokens_in", "tokens_out", "reasoning", "age", "model")
        labels = {"title": "Название", "children": "Дочер.", "status": "Сост.", "directory": "Директория", "size": "Размер",
                  "messages": "Сообщ.", "tokens_in": "Tokens In", "tokens_out": "Tokens Out",
                  "reasoning": "Reasoning", "age": "Возраст", "model": "Модель"}
        for c in cols:
            arrow = ""
            if c == self._session_sort_col:
                arrow = " ▲" if self._session_sort_asc else " ▼"
            target = "#0" if c == "title" else c
            self.tree.heading(target, text=labels[c] + arrow)

        self._load_sessions()

    # ─────────────────────────────────────────────
    # ВКЛАДКА: СООБЩЕНИЯ
    # ─────────────────────────────────────────────
    def _create_messages_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Сообщения  ")

        self.msg_session_id = None

        # Header
        header = ttk.Frame(frame)
        header.pack(fill=tk.X, padx=5, pady=5)
        self.msg_header_label = ttk.Label(header, text="Выберите сессию (двойной клик в таблице сессий)",
                                          style="Header.TLabel")
        self.msg_header_label.pack(side=tk.LEFT)

        # Actions
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, padx=5, pady=(0, 5))

        ttk.Button(actions, text="Удалить выбранные", command=self._delete_selected_messages,
                  style="Danger.TButton").pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Удалить ошибки", command=self._delete_error_messages).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Удалить reasoning", command=self._delete_reasoning_messages).pack(side=tk.LEFT, padx=2)
        ttk.Button(actions, text="Удалить старше 7 дней", command=self._delete_old_messages).pack(side=tk.LEFT, padx=2)
        ttk.Separator(actions, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=5)
        ttk.Button(actions, text="Обновить", command=self._refresh_messages).pack(side=tk.LEFT, padx=2)

        self.msg_filter_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(actions, text="Только с контентом",
                        variable=self.msg_filter_var,
                        command=self._refresh_messages).pack(side=tk.LEFT, padx=5)

        self.msg_selected_label = ttk.Label(actions, text="")
        self.msg_selected_label.pack(side=tk.RIGHT, padx=5)

        # Summary bar
        self.msg_summary = ttk.Label(frame, text="", relief=tk.SUNKEN, anchor=tk.W, padding=3)
        self.msg_summary.pack(fill=tk.X, padx=5, pady=(0, 3))

        # Splitter: parts list + detail
        paned = ttk.PanedWindow(frame, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        # Left: parts list
        left = ttk.Frame(paned)
        paned.add(left, weight=3)

        columns = ("time", "type", "status", "size", "text")
        self.msg_tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended")

        self.msg_tree.heading("time", text="Дата и время", command=self._toggle_msg_sort)
        self.msg_tree.heading("type", text="Тип", command=lambda: self._toggle_msg_sort_col("type"))
        self.msg_tree.heading("status", text="Статус", command=lambda: self._toggle_msg_sort_col("status"))
        self.msg_tree.heading("size", text="Размер", command=lambda: self._toggle_msg_sort_col("size"))
        self.msg_tree.heading("text", text="Текст / Инструмент")

        self.msg_tree.column("time", width=150, minwidth=120, stretch=False)
        self.msg_tree.column("type", width=90, minwidth=60, stretch=False)
        self.msg_tree.column("status", width=80, minwidth=50, stretch=False)
        self.msg_tree.column("size", width=70, minwidth=50, stretch=False)
        self.msg_tree.column("text", width=400, minwidth=100)

        msg_scroll = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self._on_msg_scrollbar)
        self._msg_scrollbar = msg_scroll
        self.msg_tree.configure(yscrollcommand=self._on_msg_scroll_set)

        self.msg_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        msg_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        self.msg_tree.bind("<<TreeviewSelect>>", self._on_msg_select)

        # Lazy loading state
        self._msg_page_size = 500
        self._msg_offset = 0
        self._msg_total = 0
        self._msg_loading = False
        self._msg_sort_asc = True  # True = от начала (старые), False = от конца (новые)

        # Bind scroll for lazy loading
        self.msg_tree.bind("<MouseWheel>", self._on_msg_scroll)
        self.msg_tree.bind("<Button-4>", self._on_msg_scroll)
        self.msg_tree.bind("<Button-5>", self._on_msg_scroll)

        # Right: detail
        right = ttk.Frame(paned)
        paned.add(right, weight=2)

        ttk.Label(right, text="Детали:", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W)
        self.msg_detail = tk.Text(right, font=("Segoe UI", 10), wrap=tk.WORD,
                                   relief=tk.FLAT, padx=8, pady=6)
        detail_scroll = ttk.Scrollbar(right, orient=tk.VERTICAL, command=self.msg_detail.yview)
        self.msg_detail.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.msg_detail.pack(fill=tk.BOTH, expand=True)

        # Chat-like tags
        self.msg_detail.tag_configure("role_user", font=("Segoe UI", 10, "bold"), foreground="#2563eb", spacing3=2)
        self.msg_detail.tag_configure("role_assistant", font=("Segoe UI", 10, "bold"), foreground="#16a34a", spacing3=2)
        self.msg_detail.tag_configure("role_tool", font=("Segoe UI", 10, "bold"), foreground="#9333ea", spacing3=2)
        self.msg_detail.tag_configure("text_body", font=("Segoe UI", 10), lmargin1=10, lmargin2=10, spacing1=2, spacing3=6)
        self.msg_detail.tag_configure("tool_name", font=("Consolas", 9, "bold"), foreground="#b45309")
        self.msg_detail.tag_configure("tool_ok", font=("Consolas", 9), foreground="#16a34a")
        self.msg_detail.tag_configure("tool_err", font=("Consolas", 9), foreground="#dc2626")
        self.msg_detail.tag_configure("reasoning_label", font=("Segoe UI", 9, "italic"), foreground="#6b7280")
        self.msg_detail.tag_configure("reasoning_body", font=("Consolas", 9), foreground="#6b7280", lmargin1=10, lmargin2=10, background="#f9fafb")
        self.msg_detail.tag_configure("patch_file", font=("Consolas", 9, "bold"), foreground="#7c3aed")
        self.msg_detail.tag_configure("step_marker", font=("Segoe UI", 9, "bold"), foreground="#0891b2")
        self.msg_detail.tag_configure("divider", foreground="#d1d5db")

        self._msg_parts = []
        self._msg_part_map = {}  # iid -> part dict

    def _open_messages_for_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("Инфо", "Выберите сессию в таблице")
            return
        session = self._session_map.get(sel[0])
        if session:
            self._open_messages_for_session(session.id)

    def _open_messages_for_session(self, session_id):
        self.msg_session_id = session_id
        session = self.db.get_session(session_id)
        title = session.title if session else session_id
        self.msg_header_label.config(text=f"Сессия: {title[:70]}")
        self.notebook.select(1)
        self._refresh_messages()

    def _refresh_messages(self):
        if not self.msg_session_id:
            return
        self.status_var.set("Загрузка сообщений...")
        self.update_idletasks()

        self._msg_offset = 0
        self._msg_loading = True
        self.msg_tree.delete(*self.msg_tree.get_children())
        self._msg_part_map = {}
        filter_on = self.msg_filter_var.get()

        def load():
            total = self.db.get_chat_messages_count(self.msg_session_id, has_parts_only=filter_on)
            messages = self.db.get_chat_messages(self.msg_session_id,
                                                  limit=self._msg_page_size, offset=0,
                                                  ascending=self._msg_sort_asc,
                                                  has_parts_only=filter_on)
            self.after(10, lambda: self._messages_loaded(messages, total, reset=True))

        threading.Thread(target=load, daemon=True).start()

    def _load_more_messages(self):
        if not self.msg_session_id or self._msg_loading:
            return
        if self._msg_offset >= self._msg_total:
            return

        self._msg_loading = True
        self.status_var.set(f"Загрузка... ({self._msg_offset}/{self._msg_total})")
        self.update_idletasks()
        filter_on = self.msg_filter_var.get()

        def load():
            messages = self.db.get_chat_messages(self.msg_session_id,
                                                  limit=self._msg_page_size, offset=self._msg_offset,
                                                  ascending=self._msg_sort_asc,
                                                  has_parts_only=filter_on)
            self.after(10, lambda: self._messages_loaded(messages, self._msg_total, reset=False))

        threading.Thread(target=load, daemon=True).start()

    def _on_msg_scroll_set(self, first, last):
        """Called when treeview updates its scrollbar position."""
        self._last_msg_scroll_pos = float(last)
        # Update original scrollbar
        if hasattr(self, '_msg_scrollbar'):
            self._msg_scrollbar.set(first, last)

    def _on_msg_scrollbar(self, *args):
        """Called when user drags scrollbar or clicks arrows."""
        self.msg_tree.yview(*args)
        self._check_msg_load_needed()

    def _check_msg_load_needed(self):
        """Check if we need to load more messages based on scroll position."""
        if self._msg_loading or not self.msg_session_id:
            return
        last = getattr(self, '_last_msg_scroll_pos', 1.0)
        if last > 0.85:
            self._load_more_messages()

    def _on_msg_scroll(self, event):
        if self._msg_loading or not self.msg_session_id:
            return

        # Handle different scroll events
        if event.num == 4:  # Linux scroll up
            return
        elif event.num == 5:  # Linux scroll down
            pass
        elif hasattr(event, 'delta'):
            if event.delta > 0:  # Windows scroll up
                return

        self._check_msg_load_needed()

    def _toggle_msg_sort(self):
        self._msg_sort_asc = not self._msg_sort_asc
        self.msg_tree.heading("time", text="Дата и время ▼" if not self._msg_sort_asc else "Дата и время ▲")
        self._refresh_messages()

    def _toggle_msg_sort_col(self, col):
        """Client-side sort for type/status/size columns."""
        items = [(self.msg_tree.set(k, col), k) for k in self.msg_tree.get_children("")]

        # Determine sort direction based on current state
        attr = f"_msg_{col}_sort_asc"
        current = getattr(self, attr, True)
        setattr(self, attr, not current)

        # Numeric sort for size, alpha for others
        if col == "size":
            def size_key(val):
                s = val[0]
                if "GB" in s:
                    return float(s.replace(" GB", "")) * 1024
                elif "MB" in s:
                    return float(s.replace(" MB", ""))
                elif "KB" in s:
                    return float(s.replace(" KB", "")) / 1024
                return 0
            items.sort(key=size_key, reverse=not current)
        else:
            items.sort(key=lambda t: t[0].lower(), reverse=not current)

        # Reorder tree
        for idx, (val, k) in enumerate(items):
            self.msg_tree.move(k, "", idx)

        # Update header arrow
        arrow = " ▲" if current else " ▼"
        labels = {"type": "Тип", "status": "Статус", "size": "Размер"}
        self.msg_tree.heading(col, text=labels.get(col, col) + arrow)

    def _messages_loaded(self, messages, total, reset=False):
        self._msg_total = total

        # Summary — only on reset
        if reset:
            parts_summary = {}
            for msg in messages:
                for p in msg["parts"]:
                    pt = p["type"]
                    parts_summary[pt] = parts_summary.get(pt, {"count": 0, "size": 0})
                    parts_summary[pt]["count"] += 1
                    parts_summary[pt]["size"] += p["size"]

            lines = []
            total_size = 0
            for ptype, info in sorted(parts_summary.items()):
                size_mb = info["size"] / 1024 / 1024
                total_size += info["size"]
                lines.append(f"{ptype}: {info['count']} ({size_mb:.1f} MB)")
            total_mb = total_size / 1024 / 1024
            self.msg_summary.config(text=f"Сообщений: {len(messages)} | Parts: {total_size / 1024 / 1024:.1f} MB | " + " | ".join(lines))

        # Populate tree
        for msg in messages:
            role = msg["role"]
            parts = msg["parts"]

            # Определяем отображение
            if role == "user":
                # User message — показать текст из parts или из data.summary.diffs
                text_parts = [p for p in parts if p["type"] == "text"]
                if text_parts:
                    display = text_parts[0]["text"][:120]
                else:
                    summary = msg["data"].get("summary", {})
                    diffs = summary.get("diffs", [])
                    if diffs:
                        fname = diffs[0].get("file", "")
                        display = f"[{fname}] +{diffs[0].get('additions',0)} -{diffs[0].get('deletions',0)}"[:120]
                    else:
                        display = "(компактировано)"
                ptype = "user"
                status = ""
                size = sum(p["size"] for p in parts)
            elif role == "assistant":
                # Assistant — найти основной контент
                text_parts = [p for p in parts if p["type"] == "text"]
                tool_parts = [p for p in parts if p["type"] == "tool"]
                reasoning_parts = [p for p in parts if p["type"] == "reasoning"]

                if text_parts:
                    display = text_parts[0]["text"][:120]
                    ptype = "text"
                elif tool_parts:
                    tool_name = tool_parts[0]["data"].get("tool", "")
                    display = f"{tool_name} ({len(tool_parts)} вызовов)"
                    ptype = "tool"
                elif reasoning_parts:
                    display = reasoning_parts[0]["text"][:120]
                    ptype = "reasoning"
                elif parts:
                    display = f"{parts[0]['type']} ({len(parts)} частей)"
                    ptype = parts[0]["type"]
                else:
                    display = "(нет данных)"
                    ptype = "empty"
                status = ""
                size = sum(p["size"] for p in parts)
            else:
                display = f"{role} ({len(parts)} частей)"
                ptype = role
                status = ""
                size = sum(p["size"] for p in parts)

            size_str = self._fmt_size(size)

            # Время
            try:
                dt = datetime.fromtimestamp(msg["time"] / 1000, tz=timezone.utc)
                time_str = dt.strftime("%d.%m.%Y %H:%M:%S")
            except:
                time_str = ""

            iid = self.msg_tree.insert("", tk.END, values=(
                time_str, ptype, status, size_str, display
            ))
            self._msg_part_map[iid] = msg

        self._msg_offset += len(messages)
        self._msg_loading = False
        self.status_var.set(f"Загружено {self._msg_offset} из {total} сообщений")

    def _on_msg_select(self, event):
        sel = self.msg_tree.selection()
        count = len(sel)
        if count == 0:
            self.msg_selected_label.config(text="")
        elif count == 1:
            self.msg_selected_label.config(text="Выбрано: 1 сообщение")
        else:
            self.msg_selected_label.config(text=f"Выбрано: {count} сообщений")

        if count == 1:
            item = self._msg_part_map.get(sel[0])
            if item:
                if "parts" in item:
                    self._show_chat_message(item)
                else:
                    self._show_part_detail(item)
        elif count > 1:
            total_size = 0
            types = {}
            for iid in sel:
                item = self._msg_part_map.get(iid)
                if item:
                    if "parts" in item:
                        for p in item["parts"]:
                            total_size += p["size"]
                            t = p["type"]
                            types[t] = types.get(t, 0) + 1
                    else:
                        total_size += item.get("size", 0)
                        t = item.get("type", "unknown")
                        types[t] = types.get(t, 0) + 1
            self._show_selection_summary(sel, total_size, types)

    def _show_chat_message(self, msg):
        """Показать сообщение в chat-формате."""
        self.msg_detail.config(state=tk.NORMAL)
        self.msg_detail.delete("1.0", tk.END)

        role = msg["role"]
        parts = msg["parts"]

        # Время
        try:
            dt = datetime.fromtimestamp(msg["time"] / 1000, tz=timezone.utc)
            time_str = dt.strftime("%d.%m.%Y %H:%M:%S")
        except:
            time_str = ""

        if role == "user":
            self.msg_detail.insert(tk.END, f"Вы  {time_str}\n", "role_user")
            text_parts = [p for p in parts if p["type"] == "text"]
            if text_parts:
                self.msg_detail.insert(tk.END, text_parts[0]["text"] + "\n", "text_body")
            else:
                summary = msg.get("data", {}).get("summary", {})
                diffs = summary.get("diffs", [])
                if diffs:
                    fname = diffs[0].get("file", "?")
                    adds = diffs[0].get("additions", 0)
                    dels = diffs[0].get("deletions", 0)
                    self.msg_detail.insert(tk.END, f"[{fname}] +{adds} -{dels}\n", "text_body")
                else:
                    self.msg_detail.insert(tk.END, "(компактировано)\n", "text_body")

        elif role == "assistant":
            self.msg_detail.insert(tk.END, f"Ассистент  {time_str}\n", "role_assistant")

            for p in parts:
                ptype = p["type"]
                data = p["data"]
                state = data.get("state", {})

                if ptype == "text":
                    self.msg_detail.insert(tk.END, p["text"] + "\n", "text_body")

                elif ptype == "tool":
                    tool = data.get("tool", "unknown")
                    status = state.get("status", "")
                    self.msg_detail.insert(tk.END, f"\n{tool}", "tool_name")
                    if status == "completed":
                        self.msg_detail.insert(tk.END, " OK\n", "tool_ok")
                    elif status == "error":
                        self.msg_detail.insert(tk.END, " ОШИБКА\n", "tool_err")
                    else:
                        self.msg_detail.insert(tk.END, f" {status}\n")

                    inp = state.get("input", {})
                    if isinstance(inp, dict):
                        for k, v in inp.items():
                            val = str(v)[:300] if v else ""
                            self.msg_detail.insert(tk.END, f"  {k}: ", "tool_name")
                            self.msg_detail.insert(tk.END, f"{val}\n", "text_body")

                    err = state.get("error", "")
                    if err:
                        self.msg_detail.insert(tk.END, f"  Ошибка: {err[:500]}\n", "tool_err")

                elif ptype == "reasoning":
                    text = p["text"][:1500]
                    self.msg_detail.insert(tk.END, "\nРассуждения:\n", "reasoning_label")
                    self.msg_detail.insert(tk.END, text + "\n", "reasoning_body")

                elif ptype == "patch":
                    fp = data.get("filePath", "")
                    self.msg_detail.insert(tk.END, f"\nФайл: ", "tool_name")
                    self.msg_detail.insert(tk.END, f"{fp}\n", "patch_file")

                elif ptype == "step-start":
                    self.msg_detail.insert(tk.END, "\n── Шаг ──\n", "step_marker")
                elif ptype == "step-finish":
                    pass

        self.msg_detail.config(state=tk.DISABLED)

    def _show_selection_summary(self, sel, total_size, types):
        self.msg_detail.config(state=tk.NORMAL)
        self.msg_detail.delete("1.0", tk.END)

        self.msg_detail.insert(tk.END, f"Выделено: {len(sel)} частей\n", "role_tool")
        self.msg_detail.insert(tk.END, f"Общий размер: {self._fmt_size(total_size)}\n\n", "text_body")
        self.msg_detail.insert(tk.END, "По типам:\n", "tool_name")
        for t, cnt in sorted(types.items()):
            self.msg_detail.insert(tk.END, f"  {t}: {cnt}\n", "text_body")

        self.msg_detail.config(state=tk.DISABLED)

    def _delete_selected_messages(self):
        sel = self.msg_tree.selection()
        if not sel:
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить {len(sel)} выбранных сообщений?"):
            return
        msg_ids = [self._msg_part_map[iid]["id"] for iid in sel if iid in self._msg_part_map]
        if msg_ids:
            sid = self.msg_session_id
            conn = self.db.connect(readonly=False)
            c = conn.cursor()
            for mid in msg_ids:
                c.execute("DELETE FROM part WHERE message_id = ?", (mid,))
                c.execute("DELETE FROM message WHERE id = ?", (mid,))
            conn.commit()
            self.db._update_session_counters(sid, conn)
            conn.close()
            self._refresh_messages()
            self._need_sessions_refresh = True
            self.after(100, self._load_sessions)

    def _delete_error_messages(self):
        if not self.msg_session_id:
            return
        sid = self.msg_session_id
        count = self.db.delete_parts_by_status(sid, "error")
        if count:
            self.status_var.set(f"Удалено {count} ошибок")
            self._refresh_messages()
            self._need_sessions_refresh = True
            self.after(100, self._load_sessions)

    def _delete_reasoning_messages(self):
        if not self.msg_session_id:
            return
        sid = self.msg_session_id
        count = self.db.delete_parts_by_type(sid, "reasoning")
        if count:
            self.status_var.set(f"Удалено {count} reasoning-частей")
            self._refresh_messages()
            self._need_sessions_refresh = True
            self.after(100, self._load_sessions)

    def _delete_old_messages(self):
        if not self.msg_session_id:
            return
        sid = self.msg_session_id
        count = self.db.delete_old_messages(sid, max_age_days=7)
        if count:
            self.status_var.set(f"Удалено {count} старых сообщений")
            self._refresh_messages()
            self._need_sessions_refresh = True
            self.after(100, self._load_sessions)
        else:
            messagebox.showinfo("Инфо", "Нет сообщений старше 7 дней")

    # ─────────────────────────────────────────────
    # ВКЛАДКА: ОЧИСТКА
    # ─────────────────────────────────────────────
    def _create_cleanup_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Очистка  ")

        top = ttk.LabelFrame(frame, text="Быстрые действия", padding=10)
        top.pack(fill=tk.X, padx=10, pady=10)

        row1 = ttk.Frame(top)
        row1.pack(fill=tk.X, pady=2)
        ttk.Button(row1, text="Strip Reasoning (все сессии)",
                  command=self._strip_all_reasoning).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="Strip Reasoning (выбранные)",
                  command=self._strip_selected_reasoning).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(top)
        row2.pack(fill=tk.X, pady=2)
        ttk.Button(row2, text="Удалить старые (>30 дней)",
                  command=self._delete_old_sessions).pack(side=tk.LEFT, padx=5)
        ttk.Button(row2, text="Удалить subagent сессии",
                  command=self._delete_subagent_sessions).pack(side=tk.LEFT, padx=5)

        row3 = ttk.Frame(top)
        row3.pack(fill=tk.X, pady=2)
        ttk.Button(row3, text="Очистить снапшоты",
                  command=self._clean_snapshots).pack(side=tk.LEFT, padx=5)
        ttk.Button(row3, text="Очистить осиротевшие diff'ы",
                  command=self._clean_orphans).pack(side=tk.LEFT, padx=5)
        ttk.Button(row3, text="Vacuum БД",
                  command=self._vacuum_db).pack(side=tk.LEFT, padx=5)

        mid = ttk.LabelFrame(frame, text="Оставить N последних сессий", padding=10)
        mid.pack(fill=tk.X, padx=10, pady=(0, 10))

        row4 = ttk.Frame(mid)
        row4.pack(fill=tk.X)
        ttk.Label(row4, text="Оставить:").pack(side=tk.LEFT)
        self.keep_n_var = tk.StringVar(value="10")
        ttk.Entry(row4, textvariable=self.keep_n_var, width=5).pack(side=tk.LEFT, padx=5)
        ttk.Label(row4, text="сессий, удалить остальные").pack(side=tk.LEFT)
        ttk.Button(row4, text="Выполнить", command=self._keep_latest_n).pack(side=tk.LEFT, padx=20)

        log_frame = ttk.LabelFrame(frame, text="Журнал операций", padding=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        self.cleanup_log = scrolledtext.ScrolledText(log_frame, height=10, font=("Consolas", 9),
                                                      state=tk.DISABLED, wrap=tk.WORD)
        self.cleanup_log.pack(fill=tk.BOTH, expand=True)

    # ─────────────────────────────────────────────
    # ВКЛАДКА: ДАШБОРД
    # ─────────────────────────────────────────────
    def _create_dashboard_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Дашборд  ")
        self._dash_frame = frame
        self._build_dashboard()

    def _build_dashboard(self):
        frame = self._dash_frame
        for w in frame.winfo_children():
            w.destroy()

        stats = self.db.get_stats()

        header = ttk.Frame(frame)
        header.pack(fill=tk.X, padx=10, pady=10)
        ttk.Label(header, text="Обзор хранилища OpenCode", style="Header.TLabel").pack(side=tk.LEFT)
        ttk.Button(header, text="Обновить", command=self._build_dashboard).pack(side=tk.RIGHT)

        grid = ttk.Frame(frame)
        grid.pack(fill=tk.X, padx=10, pady=5)

        # DB
        db_box = ttk.LabelFrame(grid, text="База данных", padding=10)
        db_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 5))
        self._stat_row(db_box, "DB файл:", self._fmt_size(stats.db_size_bytes))
        self._stat_row(db_box, "WAL:", self._fmt_size(stats.wal_size_bytes))
        self._stat_row(db_box, "Итого:", self._fmt_size(stats.db_size_bytes + stats.wal_size_bytes))
        ttk.Separator(db_box, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        self._stat_row(db_box, "Сессий:", str(stats.total_sessions))
        self._stat_row(db_box, "  Корневых:", str(stats.root_sessions))
        self._stat_row(db_box, "  Subagent:", str(stats.subagent_sessions))
        self._stat_row(db_box, "Сообщений:", str(stats.total_messages))
        self._stat_row(db_box, "Частей:", str(stats.total_parts))

        # FS
        fs_box = ttk.LabelFrame(grid, text="Файловая система", padding=10)
        fs_box.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5)
        self._stat_row(fs_box, "Session diffs:", f"{self._fmt_size(stats.session_diff_size_bytes)} ({stats.session_diff_count})")
        self._stat_row(fs_box, "Снапшоты:", f"{self._fmt_size(stats.snapshot_size_bytes)} ({stats.snapshot_projects})")
        total = stats.db_size_bytes + stats.wal_size_bytes + stats.session_diff_size_bytes + stats.snapshot_size_bytes
        ttk.Separator(fs_box, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        self._stat_row(fs_box, "Итого на диске:", self._fmt_size(total))

        # Bar chart
        chart_box = ttk.LabelFrame(frame, text="Разбивка хранилища", padding=10)
        chart_box.pack(fill=tk.X, padx=10, pady=5)

        components = [
            ("БД", stats.db_size_bytes + stats.wal_size_bytes),
            ("Diffs", stats.session_diff_size_bytes),
            ("Снапшоты", stats.snapshot_size_bytes),
        ]
        total = sum(c[1] for c in components) or 1
        for name, size in components:
            pct = (size / total) * 100
            row = ttk.Frame(chart_box)
            row.pack(fill=tk.X, pady=2)
            ttk.Label(row, text=f"{name}:", width=12).pack(side=tk.LEFT)
            pb = ttk.Progressbar(row, value=pct, length=300)
            pb.pack(side=tk.LEFT, padx=(5, 10))
            ttk.Label(row, text=f"{pct:.1f}% ({self._fmt_size(size)})").pack(side=tk.LEFT)

        # Top sessions
        top_box = ttk.LabelFrame(frame, text="Топ-10 сессий по размеру", padding=10)
        top_box.pack(fill=tk.BOTH, expand=True, padx=10, pady=(5, 10))

        sessions = self.db.get_sessions(sort_by="size", limit=10)
        cols = ("title", "size", "messages", "tokens_in", "reasoning", "age")
        self._dash_tree = ttk.Treeview(top_box, columns=cols, show="headings", height=10)
        self._dash_tree.heading("title", text="Название", command=lambda: self._sort_dashboard("title"))
        self._dash_tree.heading("size", text="Размер", command=lambda: self._sort_dashboard("size"))
        self._dash_tree.heading("messages", text="Сообщений", command=lambda: self._sort_dashboard("messages"))
        self._dash_tree.heading("tokens_in", text="Tokens In", command=lambda: self._sort_dashboard("tokens_in"))
        self._dash_tree.heading("reasoning", text="Reasoning", command=lambda: self._sort_dashboard("reasoning"))
        self._dash_tree.heading("age", text="Возраст", command=lambda: self._sort_dashboard("age"))
        self._dash_tree.column("title", width=300)
        self._dash_tree.column("size", width=80, stretch=False)
        self._dash_tree.column("messages", width=80, stretch=False)
        self._dash_tree.column("tokens_in", width=100, stretch=False)
        self._dash_tree.column("reasoning", width=100, stretch=False)
        self._dash_tree.column("age", width=70, stretch=False)

        for s in sessions:
            self._dash_tree.insert("", tk.END, values=(
                s.title[:60], s.size_str, s.message_count,
                f"{s.tokens_input:,}", f"{s.tokens_reasoning:,}", s.age_str
            ))
        self._dash_tree.pack(fill=tk.BOTH, expand=True)

    def _stat_row(self, parent, label, value):
        row = ttk.Frame(parent)
        row.pack(fill=tk.X, pady=1)
        ttk.Label(row, text=label, width=15, anchor=tk.W).pack(side=tk.LEFT)
        ttk.Label(row, text=value, style="Stat.TLabel").pack(side=tk.LEFT)
        return row

    def _sort_dashboard(self, col):
        items = [(self._dash_tree.set(k, col), k) for k in self._dash_tree.get_children("")]
        attr = f"_dash_{col}_sort_asc"
        current = getattr(self, attr, True)
        setattr(self, attr, not current)

        if col in ("size", "messages", "tokens_in", "reasoning"):
            def num_key(val):
                s = val[0].replace(",", "").replace(" ", "")
                if "GB" in s:
                    return float(s.replace("GB", "")) * 1024
                elif "MB" in s:
                    return float(s.replace("MB", ""))
                return float(s) if s.replace(".", "").isdigit() else 0
            items.sort(key=num_key, reverse=not current)
        else:
            items.sort(key=lambda t: t[0].lower(), reverse=not current)

        for idx, (val, k) in enumerate(items):
            self._dash_tree.move(k, "", idx)

        arrow = " ▲" if current else " ▼"
        labels = {"title": "Название", "size": "Размер", "messages": "Сообщений",
                  "tokens_in": "Tokens In", "reasoning": "Reasoning", "age": "Возраст"}
        self._dash_tree.heading(col, text=labels.get(col, col) + arrow)

    def _fmt_size(self, bytes_val):
        if bytes_val >= 1024 ** 3:
            return f"{bytes_val / 1024 ** 3:.2f} GB"
        elif bytes_val >= 1024 ** 2:
            return f"{bytes_val / 1024 ** 2:.1f} MB"
        elif bytes_val >= 1024:
            return f"{bytes_val / 1024:.1f} KB"
        return f"{bytes_val} B"

    def _create_status_bar(self):
        self.status_var = tk.StringVar(value="Готово")
        status_bar = ttk.Label(self, textvariable=self.status_var, relief=tk.SUNKEN, anchor=tk.W)
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def _on_tab_changed(self, event):
        selected = self.notebook.index(self.notebook.select())
        if selected == 0 and self._need_sessions_refresh:
            self._need_sessions_refresh = False
            self._load_sessions()

    # ─────────────────────────────────────────────
    # СЕССИИ: загрузка и фильтрация
    # ─────────────────────────────────────────────
    def _load_sessions(self):
        self.status_var.set("Загрузка сессий...")
        self.update_idletasks()

        def load():
            sessions = self.db.get_sessions(sort_by=self._session_sort_col,
                                             ascending=self._session_sort_asc)
            self.after(10, lambda: self._sessions_loaded(sessions))

        threading.Thread(target=load, daemon=True).start()

    def _sessions_loaded(self, sessions):
        self._all_sessions = sessions
        self._filter_sessions()
        self.tree.update_idletasks()
        db_label = Path(self.db.db_path).stem
        self.status_var.set(f"[{db_label}] Загружено {len(sessions)} сессий")

    def _filter_sessions(self, *args):
        search = self.search_var.get().lower()
        if not search:
            self._sessions = self._all_sessions
        else:
            matched = [s for s in self._all_sessions if search in s.title.lower() or search in s.id.lower()]
            # If a child matched, also include its parent
            child_parent_ids = {s.parent_id for s in matched if s.parent_id}
            for s in self._all_sessions:
                if s.id in child_parent_ids and s not in matched:
                    matched.append(s)
            self._sessions = matched
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._session_map = {}
        show_sub = self.subagent_var.get()
        existing_ids = {s.id for s in self._sessions}

        # Separate roots, children, and orphans
        children_of: dict[str, list[SessionInfo]] = {}
        roots = []
        orphans = []
        for s in self._sessions:
            if s.parent_id:
                if s.parent_id in existing_ids:
                    children_of.setdefault(s.parent_id, []).append(s)
                else:
                    orphans.append(s)
            else:
                roots.append(s)

        # Insert roots
        for s in roots:
            child_count = len(children_of.get(s.id, []))
            iid = self._insert_session(s, child_count=child_count)
            self._session_map[iid] = s
            # Insert children under this root
            if show_sub:
                for child in children_of.get(s.id, []):
                    child_iid = self._insert_session(child, parent_iid=iid)
                    self._session_map[child_iid] = child
                if child_count > 0:
                    self.tree.item(iid, open=True)

        # Insert orphans at top level with red tags
        if show_sub and orphans:
            sep_iid = self.tree.insert("", tk.END, text="── Orphan (родитель удалён) ──",
                                        values=("", "", "", "", "", "", "", "", ""))
            self.tree.item(sep_iid, tags=("orphan",))
            self._session_map[sep_iid] = None
            for child in orphans:
                child_iid = self._insert_session(child, parent_iid="", orphan=True)
                self._session_map[child_iid] = child

    def _insert_session(self, s, parent_iid="", child_count=0, orphan=False):
        model = s.model.split("/")[-1] if s.model else ""
        try:
            m = json.loads(s.model)
            model = m.get("id", s.model)
        except:
            pass
        # Shorten directory path
        directory = s.directory or ""
        sep = "\\"
        parts = directory.split(sep)
        if len(parts) > 3:
            if parts[0].endswith(":"):
                directory = parts[0] + sep + "..." + sep + sep.join(parts[-2:])
            else:
                directory = "..." + sep + sep.join(parts[-2:])
        # Children column: for root show count, for subagent show "→", for orphan "⚠"
        if orphan:
            children_display = "⚠"
        elif parent_iid:
            children_display = "→"
        else:
            children_display = str(child_count) if child_count > 0 else ""
        # Status column
        status_display = "🗄" if s.is_archived else ""
        iid = self.tree.insert(parent_iid, tk.END, text=s.title[:60], values=(
            children_display, status_display, directory, s.size_str, s.message_count,
            f"{s.tokens_input:,}", f"{s.tokens_output:,}",
            f"{s.tokens_reasoning:,}", s.age_str, model
        ))
        if orphan:
            self.tree.item(iid, tags=("orphan_subagent",))
        elif parent_iid and s.is_archived:
            self.tree.item(iid, tags=("archived_subagent",))
        elif parent_iid:
            self.tree.item(iid, tags=("subagent",))
        elif s.is_archived:
            self.tree.item(iid, tags=("archived",))
        return iid

    # ─────────────────────────────────────────────
    # ЭКСПОРТ / ИМПОРТ
    # ─────────────────────────────────────────────
    def _export_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Не выбраны сессии")
            return

        output_dir = filedialog.askdirectory(title="Выберите папку для экспорта")
        if not output_dir:
            return

        self.status_var.set(f"Экспорт {len(sel)} сессий...")
        self.update_idletasks()

        def export():
            success = failed = 0
            for iid in sel:
                session = self._session_map.get(iid)
                if not session:
                    continue
                output_path = os.path.join(output_dir, f"{session.id}.json")
                if self.cli.export_session(session.id, output_path):
                    success += 1
                else:
                    failed += 1
            self.after(0, lambda: self._export_done(success, failed, output_dir))

        threading.Thread(target=export, daemon=True).start()

    def _export_done(self, success, failed, output_dir):
        msg = f"Экспортировано: {success} сессий"
        if failed:
            msg += f"\nОшибок: {failed}"
        self.status_var.set(f"Экспорт завершён: {success} ок, {failed} ошибок")
        messagebox.showinfo("Экспорт завершён", msg + f"\n\nПапка: {output_dir}")

    def _export_all(self):
        if not self._all_sessions:
            messagebox.showwarning("Внимание", "Нет сессий для экспорта")
            return
        output_dir = filedialog.askdirectory(title="Выберите папку для экспорта")
        if not output_dir:
            return

        self.status_var.set(f"Экспорт {len(self._all_sessions)} сессий...")
        self.update_idletasks()

        def export():
            success = failed = 0
            for s in self._all_sessions:
                output_path = os.path.join(output_dir, f"{s.id}.json")
                if self.cli.export_session(s.id, output_path):
                    success += 1
                else:
                    failed += 1
            self.after(0, lambda: self._export_done(success, failed, output_dir))

        threading.Thread(target=export, daemon=True).start()

    def _import_session(self):
        files = filedialog.askopenfilenames(
            title="Выберите JSON-файлы сессий",
            filetypes=[("JSON", "*.json"), ("Все файлы", "*.*")]
        )
        if not files:
            return

        # Create backup before import
        db_path = self.db.db_path
        backup_path = f"{db_path}.backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        try:
            shutil.copy2(db_path, backup_path)
        except Exception as e:
            messagebox.showerror("Ошибка", f"Не удалось создать бэкап БД:\n{e}")
            return

        self.status_var.set(f"Импорт {len(files)} файлов...")
        self.update_idletasks()

        def imp():
            success = failed = 0
            for f in files:
                if self.cli.import_session(f):
                    success += 1
                else:
                    failed += 1
            self.after(0, lambda: self._import_done(success, failed, backup_path))

        threading.Thread(target=imp, daemon=True).start()

    def _import_done(self, success, failed, backup_path=None):
        msg = f"Импортировано: {success}"
        if failed:
            msg += f"\nОшибок: {failed}"
        if backup_path:
            msg += f"\n\nБэкап БД: {backup_path}"
        msg += "\n\nПроверьте что opencode работает корректно!"
        self.status_var.set(f"Импорт завершён: {success} ок, {failed} ошибок")
        messagebox.showinfo("Импорт завершён", msg)
        self._load_sessions()

    # ─────────────────────────────────────────────
    # УДАЛЕНИЕ СЕССИЙ
    # ─────────────────────────────────────────────
    def _delete_selected(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Не выбраны сессии")
            return

        if self._check_opencode():
            return

        titles = [self._session_map[iid].title[:40] for iid in sel if iid in self._session_map]
        preview = "\n".join(f"  - {t}" for t in titles[:10])
        if len(titles) > 10:
            preview += f"\n  ... и ещё {len(titles) - 10}"

        if not messagebox.askyesno("Подтверждение",
                                    f"Удалить {len(sel)} сессий?\n\n{preview}\n\nЭто необратимо!"):
            return

        self.status_var.set("Удаление сессий...")
        self.update_idletasks()

        def delete():
            deleted = 0
            for iid in sel:
                session = self._session_map.get(iid)
                if session and self.db.delete_session(session.id):
                    deleted += 1
            self.after(0, lambda: self._delete_done(deleted))

        threading.Thread(target=delete, daemon=True).start()

    def _delete_done(self, count):
        self.status_var.set(f"Удалено {count} сессий")
        self._load_sessions()

    def _archive_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        if self._check_opencode():
            return
        active = [iid for iid in sel if self._session_map.get(iid) and not self._session_map[iid].is_archived]
        if not active:
            return
        if not messagebox.askyesno("Подтверждение", f"Архивировать {len(active)} сессий?"):
            return
        def do():
            done = 0
            for iid in active:
                s = self._session_map.get(iid)
                if s and self.db.archive_session(s.id):
                    done += 1
            self.after(0, lambda: self._archive_done(done))
        threading.Thread(target=do, daemon=True).start()

    def _archive_done(self, count):
        self.status_var.set(f"Архивировано: {count}")
        self._need_sessions_refresh = True
        self._load_sessions()

    def _unarchive_selected(self):
        sel = self.tree.selection()
        if not sel:
            return
        if self._check_opencode():
            return
        archived = [iid for iid in sel if self._session_map.get(iid) and self._session_map[iid].is_archived]
        if not archived:
            return
        if not messagebox.askyesno("Подтверждение", f"Разархивировать {len(archived)} сессий?"):
            return
        def do():
            done = 0
            for iid in archived:
                s = self._session_map.get(iid)
                if s and self.db.unarchive_session(s.id):
                    done += 1
            self.after(0, lambda: self._unarchive_done(done))
        threading.Thread(target=do, daemon=True).start()

    def _unarchive_done(self, count):
        self.status_var.set(f"Разархивировано: {count}")
        self._need_sessions_refresh = True
        self._load_sessions()

    # ─────────────────────────────────────────────
    # ОЧИСТКА
    # ─────────────────────────────────────────────
    def _log(self, msg):

        def _do():
            self.cleanup_log.config(state=tk.NORMAL)
            ts = datetime.now().strftime("%H:%M:%S")
            self.cleanup_log.insert(tk.END, f"[{ts}] {msg}\n")
            self.cleanup_log.see(tk.END)
            self.cleanup_log.config(state=tk.DISABLED)
        self.after(0, _do)

    def _strip_all_reasoning(self):
        if self._check_opencode():
            return
        if not messagebox.askyesno("Подтверждение",
                                    "Удалить reasoning из ВСЕХ сессий?\n\nЭто уберёт ~77% размера БД."):
            return
        self._log("Удаление reasoning из всех сессий...")
        def do():
            count = self.db.strip_reasoning()
            self._log(f"Удалено {count} reasoning-частей")
            self._need_sessions_refresh = True
            self.after(10, lambda: messagebox.showinfo("Готово", f"Удалено {count} reasoning-частей"))
        threading.Thread(target=do, daemon=True).start()

    def _strip_selected_reasoning(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showwarning("Внимание", "Не выбраны сессии")
            return
        if self._check_opencode():
            return
        if not messagebox.askyesno("Подтверждение",
                                    f"Удалить reasoning из {len(sel)} выбранных сессий?"):
            return
        self._log(f"Удаление reasoning из {len(sel)} сессий...")
        def do():
            total = 0
            for iid in sel:
                session = self._session_map.get(iid)
                if session:
                    count = self.db.strip_reasoning(session.id)
                    total += count
                    self._log(f"  {session.title[:40]}: {count} частей")
            self._log(f"Итого удалено: {total} reasoning-частей")
            self._need_sessions_refresh = True
            self.after(10, self._load_sessions)
        threading.Thread(target=do, daemon=True).start()

    def _is_opencode_running(self) -> list[str]:
        """Check if any OpenCode process is running. Returns list of process names found."""
        found = []
        procs = ["OpenCode.exe", "OpenCode", "opencode.exe", "opencode"]
        for p in procs:
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Get-Process -Name '{p}' -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Id"],
                    capture_output=True, text=True, timeout=5
                )
                if result.stdout.strip():
                    found.append(p)
            except Exception:
                pass
        return found

    def _warn_opencode_running(self, process_names: list[str]):
        """Show warning popup + play sound if opencode is running."""
        import winsound
        winsound.MessageBeep(winsound.MB_ICONHAND)
        names = ", ".join(process_names)
        messagebox.showwarning(
            "OpenCode запущен",
            f"Обнаружен запущенный процесс: {names}\n\n"
            f"Перед этой операцией закройте OpenCode полностью.\n"
            f"Иначе изменения могут быть потеряны или база данных будет повреждена."
        )

    def _check_opencode(self) -> bool:
        """Returns True if blocked (opencode running), False if ok to proceed."""
        running = self._is_opencode_running()
        if running:
            self._warn_opencode_running(running)
            return True
        return False

    def _change_session_directory(self):
        sel = self.tree.selection()
        if len(sel) != 1:
            messagebox.showwarning("Внимание", "Выберите одну сессию")
            return
        session = self._session_map.get(sel[0])
        if not session:
            return

        # Block subagent sessions — they follow their parent
        if session.parent_id:
            parent = self.db.get_session(session.parent_id)
            pname = parent.title[:40] if parent else session.parent_id
            messagebox.showwarning(
                "Дочерняя сессия",
                f"Это дочерняя (subagent) сессия.\n"
                f"Она привязана к родительской: «{pname}»\n\n"
                f"Переносите родительскую сессию —\n"
                f"дочерние переедут автоматически."
            )
            return

        if self._check_opencode():
            return

        new_dir = filedialog.askdirectory(title=f"Новый проект для: {session.title[:40]}",
                                          initialdir=session.directory if session.directory else "")
        if not new_dir:
            return
        # Normalize: tkinter может вернуть forward slashes на Windows
        if os.name == 'nt':
            new_dir = new_dir.replace('/', '\\')
        if new_dir == session.directory:
            return
        if self.db.update_session_directory(session.id, new_dir):
            updated = self.db.get_session(session.id)
            proj_label = updated.directory if updated else new_dir
            self.status_var.set(f"Сессия перенесена: {session.title[:30]} -> {proj_label}")
            self._need_sessions_refresh = True
            self._load_sessions()
        else:
            messagebox.showerror("Ошибка", "Не удалось обновить директорию сессии")

    def _delete_old_sessions(self):
        if self._check_opencode():
            return
        if not messagebox.askyesno("Подтверждение", "Удалить все сессии старше 30 дней?"):
            return
        self._log("Удаление сессий старше 30 дней...")
        def do():
            import time
            sessions = self.db.get_sessions(sort_by="age")
            cutoff = (time.time() - 30 * 86400) * 1000
            deleted = 0
            for s in sessions:
                if s.time_created < cutoff and not s.is_subagent:
                    if self.db.delete_session(s.id):
                        deleted += 1
                        self._log(f"  Удалена: {s.title[:40]}")
            self._log(f"Удалено {deleted} старых сессий")
            self._need_sessions_refresh = True
            self.after(10, self._load_sessions)
        threading.Thread(target=do, daemon=True).start()

    def _delete_subagent_sessions(self):
        if self._check_opencode():
            return
        sessions = self.db.get_sessions()
        subagent = [s for s in sessions if s.is_subagent]
        if not subagent:
            messagebox.showinfo("Инфо", "Subagent-сессий не найдено")
            return
        if not messagebox.askyesno("Подтверждение", f"Удалить {len(subagent)} subagent-сессий?"):
            return
        self._log(f"Удаление {len(subagent)} subagent-сессий...")
        def do():
            deleted = 0
            for s in subagent:
                if self.db.delete_session(s.id):
                    deleted += 1
            self._log(f"Удалено {deleted} subagent-сессий")
            self._need_sessions_refresh = True
            self.after(10, self._load_sessions)
        threading.Thread(target=do, daemon=True).start()

    def _clean_snapshots(self):
        if self._check_opencode():
            return
        if not messagebox.askyesno("Подтверждение",
                                    "Удалить все директории снапшотов?\n\nOpenCode пересоздаст их при необходимости."):
            return
        self._log("Очистка снапшотов...")
        def do():
            count = self.db.clean_snapshots()
            self._log(f"Удалено {count} директорий снапшотов")
        threading.Thread(target=do, daemon=True).start()

    def _clean_orphans(self):
        if self._check_opencode():
            return
        self._log("Очистка осиротевших diff'ов...")
        def do():
            count = self.db.clean_orphan_diffs()
            self._log(f"Удалено {count} осиротевших diff-файлов")
        threading.Thread(target=do, daemon=True).start()

    def _vacuum_db(self):
        if self._check_opencode():
            return
        if not messagebox.askyesno("Подтверждение",
                                    "Выполнить VACUUM базы данных?\n\n"
                                    "Это освободит место, но временно удвоит размер БД.\n"
                                    "OpenCode должен быть закрыт."):
            return
        self._log("Выполнение VACUUM...")
        def do():
            stats = self.db.get_stats()
            before = stats.db_size_bytes
            success = self.db.vacuum()
            if success:
                stats2 = self.db.get_stats()
                after = stats2.db_size_bytes
                saved = max(0, before - after)
                self._log(f"VACUUM завершён: {self._fmt_size(before)} -> {self._fmt_size(after)} (сэкономлено {self._fmt_size(saved)})")
            else:
                self._log("VACUUM не удался — БД может быть заблокирована")
        threading.Thread(target=do, daemon=True).start()

    def _keep_latest_n(self):
        if self._check_opencode():
            return
        try:
            n = int(self.keep_n_var.get())
        except ValueError:
            messagebox.showerror("Ошибка", "Введите число")
            return

        sessions = self.db.get_sessions(sort_by="age")
        to_delete = sessions[n:]
        if not to_delete:
            messagebox.showinfo("Инфо", f"Только {len(sessions)} сессий, удалять нечего")
            return

        if not messagebox.askyesno("Подтверждение",
                                    f"Оставить {n} последних сессий, удалить {len(to_delete)} старых?"):
            return

        self._log(f"Оставляем {n}, удаляем {len(to_delete)}...")
        def do():
            deleted = 0
            for s in to_delete:
                if self.db.delete_session(s.id):
                    deleted += 1
            self._log(f"Удалено {deleted} сессий")
            self._need_sessions_refresh = True
            self.after(10, self._load_sessions)
        threading.Thread(target=do, daemon=True).start()

    def _show_about(self):
        messagebox.showinfo("О программе",
                            "OpenCode Session Manager v1.1\n\n"
                            "GUI-инструмент для управления сессиями OpenCode.\n"
                            "Просмотр, экспорт, импорт, очистка и оптимизация БД.\n\n"
                            "Тёмная тема как в OpenCode Desktop.\n"
                            "Запоминание выбранной темы.\n"
                            "WCAG AA-совместимый контраст.\n\n"
                            "Только стандартная библиотека Python (tkinter).")

    def _toggle_theme(self):
        self._dark_mode = not self._dark_mode
        style = ttk.Style(self)

        if self._dark_mode:
            bg = "#1a1a1a"
            fg = "#999999"
            sel_bg = "#252525"
            tree_bg = "#1e1e1e"
            tree_fg = "#aaaaaa"
            heading_bg = "#252525"
            entry_bg = "#252525"
            btn_bg = "#252525"
            btn_active = "#1f6feb"
            accent = "#58a6ff"
            self.configure(bg=bg)
            style.configure(".", background=bg, foreground=fg)
            style.configure("TFrame", background=bg)
            style.configure("TLabel", background=bg, foreground=fg)
            style.configure("Header.TLabel", background=bg, foreground=accent)
            style.configure("Stat.TLabel", background=bg, foreground=fg)
            style.configure("Treeview", background=tree_bg, foreground=tree_fg,
                           fieldbackground=tree_bg, rowheight=24,
                           selectbackground=btn_active, selectforeground="#ffffff",
                           font=("Consolas", 9))
            style.configure("Treeview.Heading", background=heading_bg, foreground=fg,
                           font=("Segoe UI", 9, "bold"))
            if hasattr(self, 'tree'):
                self.tree.tag_configure("subagent", foreground="#8b949e", font=("Consolas", 9, "italic"))
                self.tree.tag_configure("archived", foreground="#8b949e")
                self.tree.tag_configure("archived_subagent", foreground="#6b7280", font=("Consolas", 9, "italic"))
                self.tree.tag_configure("orphan", foreground="#f85149")
                self.tree.tag_configure("orphan_subagent", foreground="#f85149", font=("Consolas", 9, "italic"))
            style.configure("TButton", background=btn_bg, foreground=fg, padding=4)
            style.configure("TEntry", fieldbackground=entry_bg, foreground=fg)
            style.configure("TCombobox", fieldbackground=entry_bg, foreground=fg)
            style.configure("TCheckbutton", background=bg, foreground=fg)
            style.configure("TPanedwindow", background=bg)
            style.configure("TLabelframe", background=bg, foreground=fg)
            style.configure("TLabelframe.Label", background=bg, foreground=accent)
            style.configure("TNotebook", background=bg)
            style.configure("TNotebook.Tab", background=btn_bg, foreground="#999999",
                           padding=[12, 6], font=("Segoe UI", 9))
            style.map("TNotebook.Tab",
                      background=[("selected", btn_active)],
                      foreground=[("selected", "#ffffff")])
            style.configure("TScrollbar", background="#777777", troughcolor=bg)
            style.configure("TSeparator", background=sel_bg)
            style.configure("Danger.TButton", background="#da3633", foreground="#ffffff")
            style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff",
                            font=("Segoe UI", 10, "bold"), padding=6)
            style.map("Accent.TButton",
                      background=[("active", "#1d4ed8"), ("pressed", "#1e40af")],
                      foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])

            # Chat detail tags
            if hasattr(self, 'msg_detail'):
                self.msg_detail.configure(bg=tree_bg, fg=fg, insertbackground=fg)
                self.msg_detail.tag_configure("role_user", foreground="#58a6ff")
                self.msg_detail.tag_configure("role_assistant", foreground="#3fb950")
                self.msg_detail.tag_configure("role_tool", foreground="#bc8cff")
                self.msg_detail.tag_configure("text_body", foreground=fg)
                self.msg_detail.tag_configure("tool_name", foreground="#f0883e")
                self.msg_detail.tag_configure("tool_ok", foreground="#3fb950")
                self.msg_detail.tag_configure("tool_err", foreground="#f85149")
                self.msg_detail.tag_configure("reasoning_label", foreground="#8b949e")
                self.msg_detail.tag_configure("reasoning_body", foreground="#8b949e", background="#1a1a1a")
                self.msg_detail.tag_configure("patch_file", foreground="#bc8cff")
                self.msg_detail.tag_configure("step_marker", foreground="#58a6ff")

            # Help text widget
            if hasattr(self, '_help_text'):
                self._help_text.configure(bg=tree_bg, fg=fg, insertbackground=fg)
                self._help_text.tag_configure("h1", foreground=accent)
                self._help_text.tag_configure("h2", foreground="#bc8cff")
                self._help_text.tag_configure("h3", foreground=fg)
                self._help_text.tag_configure("code", background=sel_bg, foreground="#f0883e")
                self._help_text.tag_configure("table_header", background=heading_bg, foreground=fg)
                self._help_text.tag_configure("table_cell", foreground=fg)
                self._help_text.tag_configure("tip", foreground="#3fb950")
                self._help_text.tag_configure("warn", foreground="#f85149")
                self._help_text.tag_configure("gray", foreground="#8b949e")
                self._help_text.tag_configure("search_highlight", background="#1f6feb", foreground="#ffffff")

            # Search listbox
            if hasattr(self, '_help_search_list'):
                self._help_search_list.configure(bg=entry_bg, fg=fg,
                                                  selectbackground=btn_active,
                                                  selectforeground="#ffffff")

            # Cleanup log
            if hasattr(self, 'cleanup_log'):
                self.cleanup_log.configure(bg=tree_bg, fg=fg, insertbackground=fg)

            self._theme_label.set("Светлая тема")
        else:
            style.configure(".", background="SystemButtonFace", foreground="black")
            style.configure("TFrame", background="SystemButtonFace")
            style.configure("TLabel", background="SystemButtonFace", foreground="black")
            style.configure("Header.TLabel", background="SystemButtonFace", foreground="#2563eb",
                           font=("Segoe UI", 11, "bold"))
            style.configure("Stat.TLabel", background="SystemButtonFace", foreground="black",
                           font=("Consolas", 10))
            style.configure("Treeview", background="white", foreground="black",
                           fieldbackground="white", rowheight=24, font=("Consolas", 9),
                           selectbackground="#2563eb", selectforeground="white")
            style.configure("Treeview.Heading", background="#e5e7eb", foreground="black",
                           font=("Segoe UI", 9, "bold"))
            if hasattr(self, 'tree'):
                self.tree.tag_configure("subagent", foreground="#6b7280", font=("Consolas", 9, "italic"))
                self.tree.tag_configure("orphan", foreground="#dc2626")
                self.tree.tag_configure("orphan_subagent", foreground="#dc2626", font=("Consolas", 9, "italic"))
                self.tree.tag_configure("archived", foreground="#9ca3af")
                self.tree.tag_configure("archived_subagent", foreground="#9ca3af", font=("Consolas", 9, "italic"))
            style.configure("TButton", padding=4, background="#e5e7eb", foreground="black")
            style.configure("TEntry", fieldbackground="white", foreground="black")
            style.configure("TCombobox", fieldbackground="white", foreground="black")
            style.configure("TCheckbutton", background="SystemButtonFace", foreground="black")
            style.configure("TPanedwindow", background="SystemButtonFace")
            style.configure("TLabelframe", background="SystemButtonFace", foreground="black")
            style.configure("TLabelframe.Label", background="SystemButtonFace", foreground="#2563eb")
            style.configure("TNotebook", background="SystemButtonFace")
            style.configure("TNotebook.Tab", background="#e5e7eb", foreground="black",
                           padding=[12, 6], font=("Segoe UI", 9))
            style.map("TNotebook.Tab",
                      background=[("selected", "white")],
                      foreground=[("selected", "black")])
            style.configure("TScrollbar", background="#cbd5e1", troughcolor="#f1f5f9")
            style.configure("TSeparator", background="#cbd5e1")
            style.configure("Danger.TButton", background="#e5e7eb", foreground="red")
            style.configure("Accent.TButton", background="#2563eb", foreground="#ffffff",
                            font=("Segoe UI", 10, "bold"), padding=6)
            style.map("Accent.TButton",
                      background=[("active", "#1d4ed8"), ("pressed", "#1e40af")],
                      foreground=[("active", "#ffffff"), ("pressed", "#ffffff")])
            self.configure(bg="SystemButtonFace")

            if hasattr(self, '_help_text'):
                self._help_text.configure(bg="white", fg="black", insertbackground="black")
                self._help_text.tag_configure("h1", foreground="black")
                self._help_text.tag_configure("h2", foreground="#2563eb")
                self._help_text.tag_configure("h3", foreground="#374151")
                self._help_text.tag_configure("code", background="#f3f4f6", foreground="black")
                self._help_text.tag_configure("table_header", background="#e5e7eb", foreground="black")
                self._help_text.tag_configure("table_cell", foreground="black")
                self._help_text.tag_configure("tip", foreground="#16a34a")
                self._help_text.tag_configure("warn", foreground="#dc2626")
                self._help_text.tag_configure("gray", foreground="#6b7280")
                self._help_text.tag_configure("search_highlight", background="#bfdbfe", foreground="#1e40af")

            if hasattr(self, '_help_search_list'):
                self._help_search_list.configure(bg="white", fg="black",
                                                  selectbackground="#2563eb",
                                                  selectforeground="white")

            if hasattr(self, 'msg_detail'):
                self.msg_detail.configure(bg="white", fg="black", insertbackground="black")
                self.msg_detail.tag_configure("role_user", foreground="#2563eb")
                self.msg_detail.tag_configure("role_assistant", foreground="#16a34a")
                self.msg_detail.tag_configure("role_tool", foreground="#9333ea")
                self.msg_detail.tag_configure("text_body", foreground="black")
                self.msg_detail.tag_configure("tool_name", foreground="#b45309")
                self.msg_detail.tag_configure("tool_ok", foreground="#16a34a")
                self.msg_detail.tag_configure("tool_err", foreground="#dc2626")
                self.msg_detail.tag_configure("reasoning_label", foreground="#6b7280")
                self.msg_detail.tag_configure("reasoning_body", foreground="#6b7280", background="#f9fafb")
                self.msg_detail.tag_configure("patch_file", foreground="#7c3aed")
                self.msg_detail.tag_configure("step_marker", foreground="#0891b2")

            if hasattr(self, 'cleanup_log'):
                self.cleanup_log.configure(bg="white", fg="black", insertbackground="black")

            self._theme_label.set("Тёмная тема")

        self._save_config()
        self.update()

    # ─────────────────────────────────────────────
    # ВКЛАДКА: СПРАВКА
    # ─────────────────────────────────────────────
    def _create_help_tab(self):
        frame = ttk.Frame(self.notebook)
        self.notebook.add(frame, text="  Справка  ")

        # Left: navigation
        nav_frame = ttk.Frame(frame, width=220)
        nav_frame.pack(side=tk.LEFT, fill=tk.Y, padx=(5, 0), pady=5)
        nav_frame.pack_propagate(False)

        ttk.Label(nav_frame, text="Поиск", font=("Segoe UI", 9, "bold")).pack(anchor=tk.W, pady=(0, 2))
        self._help_search_var = tk.StringVar()
        self._help_search_var.trace_add("write", self._on_help_search)
        self._help_search_entry = ttk.Entry(nav_frame, textvariable=self._help_search_var)
        self._help_search_entry.pack(fill=tk.X, pady=(0, 5))
        self._help_search_entry.bind("<KeyRelease>", self._on_help_search_key)

        # Search results listbox (hidden by default)
        self._help_search_list = tk.Listbox(nav_frame, height=0, font=("Segoe UI", 9),
                                            relief=tk.SUNKEN, borderwidth=1,
                                            activestyle="none")
        self._help_search_list.pack(fill=tk.X, pady=(0, 5))
        self._help_search_list.pack_forget()
        self._help_search_list.bind("<<ListboxSelect>>", self._on_help_search_select)
        self._help_search_list.bind("<ButtonRelease-1>", self._on_help_search_click)
        self._help_search_results = []

        self._help_nav_sep = ttk.Separator(nav_frame, orient=tk.HORIZONTAL)
        self._help_nav_sep.pack(fill=tk.X, pady=2)
        ttk.Label(nav_frame, text="Навигация", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W, pady=(5, 5))

        self._help_nav_buttons_frame = ttk.Frame(nav_frame)
        self._help_nav_buttons_frame.pack(fill=tk.X)

        nav_items = [
            ("1. О программе", "overview"),
        ]

        self._help_nav_data = nav_items
        self._help_buttons = []
        for label, tag in nav_items:
            btn = ttk.Button(self._help_nav_buttons_frame, text=label,
                             command=lambda t=tag: self._scroll_to_tag(t))
            btn.pack(fill=tk.X, pady=1)
            self._help_buttons.append((tag, btn))

        ttk.Separator(nav_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)

        self._docs_btn = ttk.Button(self._help_nav_buttons_frame,
                                     text="\u041e\u0442\u043a\u0440\u044b\u0442\u044c \u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442\u0430\u0446\u0438\u044e \u2192",
                                     command=self._launch_docs_server,
                                     style="Accent.TButton")
        self._docs_btn.pack(fill=tk.X, pady=5)
        self._docs_label = ttk.Label(self._help_nav_buttons_frame, text="",
                                     font=("Segoe UI", 8), foreground="#64748b")
        self._docs_label.pack(anchor=tk.W)

        ttk.Label(nav_frame, text="", font=("Segoe UI", 8)).pack(anchor=tk.W, fill=tk.Y, expand=True)
        ttk.Label(nav_frame, text="\u0421\u043f\u0440\u0430\u0432\u043a\u0430 v2.0",
                  font=("Segoe UI", 8), foreground="#94a3b8").pack(anchor=tk.W)

        # Right: content
        content_frame = ttk.Frame(frame)
        content_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        self._help_text = tk.Text(content_frame, font=("Segoe UI", 10), wrap=tk.WORD,
                                   relief=tk.FLAT, padx=15, pady=10)
        help_scroll = ttk.Scrollbar(content_frame, orient=tk.VERTICAL, command=self._help_text.yview)
        self._help_text.configure(yscrollcommand=help_scroll.set)

        self._help_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        help_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # Configure tags for styling
        self._help_text.tag_configure("h1", font=("Segoe UI", 16, "bold"), spacing1=10, spacing3=8)
        self._help_text.tag_configure("h2", font=("Segoe UI", 13, "bold"), spacing1=12, spacing3=6, foreground="#2563eb")
        self._help_text.tag_configure("h3", font=("Segoe UI", 11, "bold"), spacing1=8, spacing3=4, foreground="#374151")
        self._help_text.tag_configure("bold", font=("Segoe UI", 10, "bold"))
        self._help_text.tag_configure("code", font=("Consolas", 10), background="#f3f4f6", relief=tk.SUNKEN, borderwidth=1)
        self._help_text.tag_configure("table_header", font=("Segoe UI", 10, "bold"), background="#e5e7eb")
        self._help_text.tag_configure("table_cell", font=("Consolas", 9))
        self._help_text.tag_configure("indent", lmargin1=20, lmargin2=20)
        self._help_text.tag_configure("bullet", lmargin1=20, lmargin2=35)
        self._help_text.tag_configure("tip", foreground="#16a34a", font=("Segoe UI", 10, "italic"))
        self._help_text.tag_configure("warn", foreground="#dc2626", font=("Segoe UI", 10, "bold"))
        self._help_text.tag_configure("gray", foreground="#6b7280")
        self._help_text.tag_configure("search_highlight", background="#bfdbfe", foreground="#1e40af")

        self._help_section_pos = {}
        self._build_help_content()

    def _on_help_search(self, *args):
        self._on_help_search_key()

    def _on_help_search_key(self, event=None):
        text = self._help_search_var.get().strip().lower()
        if not text:
            self._help_search_list.pack_forget()
            return

        # Search help text content
        matches = []
        search_from = "1.0"
        max_results = 20
        while len(matches) < max_results:
            pos = self._help_text.search(text, search_from, tk.END, nocase=True)
            if not pos:
                break
            line = int(pos.split(".")[0])
            line_text = self._help_text.get(f"{line}.0", f"{line}.0 lineend").strip()
            if len(line_text) > 80:
                line_text = line_text[:77] + "..."
            matches.append((pos, line_text))
            search_from = f"{line + 1}.0"

        self._help_search_list.delete(0, tk.END)
        if matches:
            shown = set()
            for pos, line_text in matches:
                key = line_text[:40]
                if key not in shown:
                    shown.add(key)
                    display = line_text[:65]
                    self._help_search_list.insert(tk.END, display)
            self._help_search_results = [(p, t) for p, t in matches]
            self._help_search_list.configure(height=min(len(self._help_search_results), 8))
        else:
            self._help_search_list.insert(tk.END, "(нет совпадений)")
            self._help_search_list.configure(height=1)
            self._help_search_results = []
        self._help_search_list.pack(fill=tk.X, pady=(0, 5), before=self._help_nav_sep)

    def _on_help_search_select(self, event):
        pass

    def _on_help_search_click(self, event):
        sel = self._help_search_list.curselection()
        if not sel or not hasattr(self, '_help_search_results'):
            return
        idx = sel[0]
        if idx < len(self._help_search_results):
            pos, _ = self._help_search_results[idx]
            # Remove previous highlight
            self._help_text.tag_remove("search_highlight", "1.0", tk.END)
            # Highlight the matched line
            line = int(pos.split(".")[0])
            self._help_text.tag_add("search_highlight", f"{line}.0", f"{line}.0 lineend")
            self._help_text.see(pos)
            self._help_text.focus_set()

    def _scroll_to_tag(self, tag):
        pos = self._help_section_pos.get(tag)
        if pos:
            self._help_text.see(pos)
            self._help_text.focus_set()

    def _h(self, text, tag="h2"):
        self._help_text.insert(tk.END, text + "\n", tag)

    def _p(self, text, tag=None):
        self._help_text.insert(tk.END, text + "\n", tag or ())

    def _b(self, text):
        self._help_text.insert(tk.END, text, "bold")

    def _code(self, text):
        self._help_text.insert(tk.END, text + "\n", "code")

    def _bullet(self, text):
        self._help_text.insert(tk.END, f"  {text}\n", "bullet")

    def _tip(self, text):
        self._help_text.insert(tk.END, f"  {text}\n", "tip")

    def _warn(self, text):
        self._help_text.insert(tk.END, f"  {text}\n", "warn")

    def _sep(self):
        self._help_text.insert(tk.END, "\n" + "─" * 60 + "\n\n", "gray")

    def _table_row(self, cells, header=False):
        tag = "table_header" if header else "table_cell"
        line = " | ".join(f"{c:<30}" if not header else f"{c:<30}" for c in cells)
        self._help_text.insert(tk.END, line + "\n", tag)

    def _build_help_content(self):
        self._help_text.config(state=tk.NORMAL)
        self._help_text.delete("1.0", self._help_text.index("end"))
        self._help_section_pos = {}

        # ═══════════════════════════════════════════════
        # 1. OVERVIEW (minimal — full docs in browser)
        # ═══════════════════════════════════════════════
        self._help_section_pos["overview"] = self._help_text.index("end-1c")
        self._h("1. О программе", "h1")
        self._p("OpenCode Session Manager — GUI-инструмент для управления базами данных OpenCode.")
        self._p("Позволяет просматривать, архивировать, удалять и переносить сессии,")
        self._p("чистить reasoning-токены, выполнять vacuum, экспорт/импорт, и многое другое.")
        self._p("")
        self._p("Полная документация доступна во встроенном веб-сервере:")
        self._p("Нажмите кнопку «Открыть документацию» слева, чтобы запустить")
        self._p("локальный HTTP-сервер на порту 8765 и открыть браузер.")
        self._p("")
        self._p("В браузере доступны:")
        self._bullet("Полная схема данных SQLite (все 7 таблиц, DDL, ER-диаграмма)")
        self._bullet("MCP-инспектор — 6 инструментов с параметрами и примерами")
        self._bullet("30+ SQL-запросов для диагностики (архив, orphan, проекты, статистика)")
        self._bullet("Диагностика и решение проблем (20+ сценариев)")
        self._bullet("Архитектура OpenCode: Desktop vs CLI, Tauri, channel-based DB")
        self._bullet("Миграция сессий между БД (экспорт/импорт, прямой SQL)")
        self._bullet("Feature-документация: выбор БД, дерево, архив, перенос")
        self._bullet("Глоссарий, FAQ, пошаговые гайды")
        self._p("")

        self._h("1.1 Быстрый старт", "h2")
        self._p("1. Нажмите «Открыть документацию» в левой панели")
        self._p("2. Или запустите вручную: python docs_server.py")
        self._p("3. Откроется браузер с landing page")
        self._p("4. Выберите нужный раздел из карточек")
        self._p("")
        self._p("Системные требования: Python 3.10+, tkinter (входит в поставку Python).")
        self._p("Дополнительных зависимостей не требуется.")
        self._p("")

        self._h("1.2 Основные возможности", "h2")
        self._bullet("Иерархическое дерево сессий (родитель → subagent → orphan)")
        self._bullet("Архивация/разархивация (флаг time_archived)")
        self._bullet("Strip reasoning (удаление Chain of Thought, ~77% БД)")
        self._bullet("Экспорт/импорт сессий в JSON (с авто-бэкапом)")
        self._bullet("Перенос между проектами с каскадом на subagent")
        self._bullet("Vacuum, очистка снапшотов и orphan-diff'ов")
        self._bullet("Выбор БД из выпадающего списка (opencode*.db)")
        self._bullet("MCP-инспектор (6 инструментов для AI-агента)")
        self._sep()

        self._help_text.config(state=tk.DISABLED)

    def _launch_docs_server(self):
        import subprocess, tempfile
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'docs_server.py')
        if not os.path.isfile(script):
            self._docs_label.config(text="Ошибка: docs_server.py не найден", foreground="#dc2626")
            return
        try:
            port_file = os.path.join(tempfile.gettempdir(), 'opencode_docs_port.txt')
            proc = subprocess.Popen(
                [sys.executable, script],
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
            )
            import time as _t; _t.sleep(1)
            url = None
            if os.path.isfile(port_file):
                try:
                    with open(port_file, 'r') as f:
                        url = f.read().strip()
                except: pass
            if url:
                self._docs_label.config(text=f"Сервер запущен: {url}", foreground="#16a34a")
            else:
                self._docs_label.config(text="Сервер запущен (проверьте браузер)", foreground="#16a34a")
        except Exception as e:
            self._docs_label.config(text=f"Ошибка: {e}", foreground="#dc2626")
        self._bullet("Двойной клик — открыть вкладку «Сообщения» для этой сессии")
        self._p("")
        self._tip("  Внизу отображается «Выбрано: N». Вы всегда видите сколько выбрано.")
        self._p("")

        self._h("2.5 Кнопки панели действий (подробно)", "h2")
        self._table_row(["Кнопка", "Действие", "Ограничения", "Безопасность"], header=True)
        self._table_row(["Экспорт выбранных", "Сохраняет JSON выбранных сессий в папку",
                         "Нужен хотя бы 1 выбранный", "Только чтение, безопасно"])
        self._table_row(["Экспорт всех", "Экспорт всех сессий текущей БД",
                         "—", "Только чтение, безопасно"])
        self._table_row(["Импорт JSON", "Загружает сессии из JSON, создаёт бэкап БД",
                         "Файлы .json", "Пишет в БД (авто-бэкап)"])
        self._table_row(["Удалить выбранные", "DELETE from session + message + part",
                         "Безвозвратно", "Требует подтверждения + OpenCode закрыт"])
        self._table_row(["Strip reasoning", "DELETE FROM part WHERE type='reasoning'",
                         "Необратимо (для выбранных)", "~77% экономии, диалог сохраняется"])
        self._table_row(["Открыть сообщения", "Переход на вкладку «Сообщения»",
                         "Нужна 1 сессия", "Только чтение"])
        self._table_row(["Перенести в проект", "UPDATE directory, path, project_id",
                         "Не для subagent (disabled)", "Требует OpenCode закрыт"])
        self._table_row(["Архивировать", "SET time_archived = now",
                         "Только для активных сессий", "Требует OpenCode закрыт"])
        self._table_row(["Разархивировать", "SET time_archived = NULL",
                         "Только для архивных сессий", "Требует OpenCode закрыт"])
        self._p("")
        self._warn("  Все операции записи блокируются если OpenCode запущен!")
        self._tip("  Экспорт — единственный безопасный способ сохранить сессию перед удалением.")
        self._p("")

        self._h("2.6 Горячие клавиши", "h2")
        self._table_row(["Клавиша", "Действие"], header=True)
        self._table_row(["F5", "Обновить список сессий"])
        self._table_row(["Ctrl+Click", "Добавить/убрать из множественного выбора"])
        self._table_row(["Shift+Click", "Выбрать диапазон"])
        self._table_row(["Double Click", "Открыть сообщения сессии"])
        self._p("")

        self._h("2.7 Пример: полный цикл работы с сессией", "h2")
        self._code("  1. Откройте Session Manager")
        self._code("  2. Выберите БД в тулбаре (если нужно)")
        self._code("  3. Найдите нужную сессию через поиск или сортировку")
        self._code("  4. Двойной клик → вкладка «Сообщения» → просмотрите диалог")
        self._code("  5. Если сессия тяжёлая: удалите reasoning через вкладку «Сообщения»")
        self._code("  6. Если нужно перенести: выделите → «Перенести в проект»")
        self._code("  7. Если нужно сохранить: выделите → «Экспорт выбранных»")
        self._code("  8. Если нужно удалить: выделите → «Удалить выбранные» (необратимо!)")
        self._p("")

        self._h("2.8 Пример: найти и удалить самую большую сессию", "h2")
        self._code("  1. Кликните по заголовку «Размер» дважды (▼)")
        self._code("  2. Самая большая сессия — первая в списке")
        self._code("  3. Выберите её (один клик)")
        self._code("  4. Нажмите «Экспорт выбранных» → сохраните бэкап")
        self._code("  5. Нажмите «Удалить выбранные» → подтвердите")
        self._tip("  Результат: сессия удалена вместе с diff-файлом.")
        self._sep()

        # ═══════════════════════════════════════════════
        # 2.8 ARCHIVE
        # ═══════════════════════════════════════════════
        self._help_section_pos["archive"] = self._help_text.index("end-1c")
        self._h("2.9 Архивация сессий", "h1")
        self._p("Архивация скрывает сессию из основного списка без удаления данных.")
        self._p("Колонка «Сост.» показывает статус: 🗄 = архивная, пусто = активная.")
        self._p("Архивные строки выделяются серым цветом (#8b949e тёмная / #9ca3af светлая).")
        self._p("")
        self._h("2.9.1 Как работает", "h2")
        self._p("В БД есть колонка time_archived (INTEGER, timestamp ms):")
        self._code("  NULL            → сессия активна, видна в opencode")
        self._code("  1780381278753   → сессия архивирована, opencode её скрывает")
        self._p("opencode (TUI/Desktop) по умолчанию не показывает архивные сессии.")
        self._p("")
        self._h("2.9.2 Кнопки", "h2")
        self._table_row(["Кнопка", "SQL-запрос", "Когда активна"], header=True)
        self._table_row(["Архивировать", "UPDATE ... SET time_archived = ?", "Выбраны активные сессии"])
        self._table_row(["Разархивировать", "UPDATE ... SET time_archived = NULL", "Выбраны архивные сессии"])
        self._p("")
        self._h("2.9.3 Защита", "h2")
        self._p("Обе кнопки проверяют _check_opencode() — блокируются если OpenCode запущен.")
        self._p("")
        self._h("2.9.4 Важно", "h2")
        self._p("Архивация не удаляет данные — только устанавливает флаг.")
        self._p("Session_diff и снапшоты остаются нетронутыми.")
        self._p("Архивные сессии можно разархивировать в любой момент.")
        self._p("")

        # ═══════════════════════════════════════════════
        # 2.10 MOVE SESSION
        # ═══════════════════════════════════════════════
        self._help_section_pos["move"] = self._help_text.index("end-1c")
        self._h("2.10 Перенос сессии в другой проект", "h1")
        self._h("2.10.1 Как работает", "h2")
        self._p("Кнопка «Перенести в проект» меняет три поля в таблице session:")
        self._code("  directory  = Q:\\User_Data\\Desktop\\НовыйПроект")
        self._code("  path       = Q:/User_Data/Desktop/НовыйПроект (forward slashes)")
        self._code("  project_id = <git-хэш корня> или 'global'")
        self._p("")
        self._p("project_id определяется через git:")
        self._code("  1. git -C <новая_директория> rev-parse --show-toplevel")
        self._code("  2. Поиск совпадения в таблице project")
        self._code("  3. Если не найден — 'global'")
        self._p("")
        self._p("Дочерние subagent-сессии (parent_id IS NOT NULL) переносятся ")
        self._p("автоматически через каскадный UPDATE WHERE parent_id = ?.")
        self._p("")
        self._h("2.10.2 Ограничения", "h2")
        self._bullet("Subagent-сессии нельзя перенести отдельно (кнопка disabled)")
        self._bullet("Если открыт диалог выбора папки, tkinter на Windows может вернуть / вместо \\")
        self._p("  (исправлено: replace('/', '\\\\') после диалога)", tag="indent")
        self._bullet("Diff-файлы не перемещаются — они остаются в старой сессии")
        self._bullet("Пути внутри diff'ов не меняются — они относительны корня проекта")
        self._p("")
        self._h("2.10.3 Защита", "h2")
        self._p("Перед переносом проверяется, запущен ли OpenCode.")
        self._p("Если процесс обнаружен — операция блокируется с предупреждением.")
        self._code("  Проверка через: Get-Process -Name 'OpenCode.exe','opencode'")
        self._p("")

        # ═══════════════════════════════════════════════
        # 2.11 HIERARCHICAL TREE
        # ═══════════════════════════════════════════════
        self._help_section_pos["hierarchy"] = self._help_text.index("end-1c")
        self._h("2.11 Иерархическое дерево (родитель → subagent)", "h1")
        self._p("Сессии отображаются в виде дерева, а не плоского списка.")
        self._p("")
        self._h("2.11.1 Структура", "h2")
        self._p("Корневые сессии (parent_id IS NULL) — верхний уровень.")
        self._p("Дочерние subagent-сессии (parent_id = ID родителя) — под родителем с отступом.")
        self._p("Колонка «Дочер.» показывает:")
        self._bullet("Число — сколько subagent'ов у корневой сессии")
        self._bullet("→ — строка является subagent'ом")
        self._bullet("⚠ — orphan (родитель удалён)")
        self._p("")
        self._h("2.11.2 Subagent-сессии", "h2")
        self._p("Создаются встроенными агентами OpenCode: @explore, @general.")
        self._p("Обычно короткие, не имеют самостоятельной ценности.")
        self._p("В opencode они скрыты из основного списка — видны только внутри родителя.")
        self._p("Менеджер показывает их в дереве под родителем (если чекбокс «Sub» включён).")
        self._p("")
        self._h("2.11.3 Orphan-сессии (красные)", "h2")
        self._p("Если родительская сессия была удалена, её дети становятся orphan'ами.")
        self._p("Они отображаются в отдельной секции «── Orphan (родитель удалён) ──»")
        self._p("красным цветом (#dc2626 / #f85149) с символом ⚠ в колонке «Дочер.».")
        self._p("")
        self._h("2.11.4 Чекбокс «Sub»", "h2")
        self._p("В тулбаре. Включает/выключает показ subagent-сессий.")
        self._p("По умолчанию включён. При выключении показываются только корневые.")
        self._p("")

        # ═══════════════════════════════════════════════
        # 2.12 DB SELECTOR
        # ═══════════════════════════════════════════════
        self._help_section_pos["dbselector"] = self._help_text.index("end-1c")
        self._h("2.12 Выбор базы данных (DB Selector)", "h1")
        self._h("2.12.1 Зачем", "h2")
        self._p("На системе может быть несколько SQLite-баз OpenCode:")
        self._table_row(["Файл", "Откуда", "Сессий"], header=True)
        self._table_row(["opencode.db", "Desktop-приложение / старая версия", "56-60"])
        self._table_row(["opencode-dev.db", "Dev-сборка из npm", "3-7"])
        self._table_row(["opencode1.db", "Копия/бэкап", "~51"])
        self._table_row(["opencode - копия.db", "Системная копия", "~51"])
        self._p("Селектор позволяет переключаться между ними без перезапуска.")
        self._p("")
        self._h("2.12.2 Автоопределение", "h2")
        self._p("При запуске сканируются все opencode*.db в ~/.local/share/opencode/.")
        self._p("Выбирается БД с МАКСИМАЛЬНЫМ количеством сессий.")
        self._p("Если количество одинаково — приоритет у opencode.db.")
        self._p("")
        self._h("2.12.3 Как использовать", "h2")
        self._p("Выпадающий список в тулбаре: «opencode (60 сес.)».")
        self._p("Выберите другую БД — сессии перезагрузятся автоматически.")
        self._p("Статус-бар показывает активную БД: «[opencode] Загружено 60 сессий».")
        self._p("")

        # ═══════════════════════════════════════════════
        # 2.13 MCP
        # ═══════════════════════════════════════════════
        self._help_section_pos["mcp"] = self._help_text.index("end-1c")
        self._h("2.13 MCP-инспектор баз opencode", "h1")
        self._p("MCP-сервер mcp-opencode-db.py — инструмент для диагностики БД изнутри opencode-сессии.")
        self._p("Подключён в глобальном opencode.jsonc. Инструменты доступны LLM-агенту.")
        self._p("")
        self._h("2.13.1 Инструменты", "h2")
        self._table_row(["Инструмент", "Параметры", "Описание"], header=True)
        self._table_row(["oc_list_dbs", "—", "Список всех opencode*.db (путь, имя, сессий, размер)"])
        self._table_row(["oc_list_sessions", "db, project_id?, parent_id?, search?, limit?",
                         "Сессии с фильтрацией, включая число детей"])
        self._table_row(["oc_get_session", "db, session_id",
                         "Полная сессия: метаданные, сообщения, parts"])
        self._table_row(["oc_get_children", "db, parent_id, recursive?",
                         "Дочерние сессии рекурсивно (до глубины 10)"])
        self._table_row(["oc_check_orphans", "—",
                         "Сравнение opencode.db vs opencode-dev.db"])
        self._table_row(["oc_query", "db, sql",
                         "Read-only SQL (только SELECT)"])
        self._p("")
        self._h("2.13.2 Подключение", "h2")
        self._code("  Файл: C:\\Users\\.config\\opencode\\opencode.jsonc")
        self._code("  Команда: python.exe Q:\\...\\mcp-opencode-db.py")
        self._p("После изменения конфига нужен перезапуск opencode.")
        self._p("На Windows указывайте ПОЛНЫЙ путь к python.exe — opencode не видит PATH.")
        self._p("")

        # ═══════════════════════════════════════════════
        # 2.14 KNOWN ISSUES
        # ═══════════════════════════════════════════════
        self._help_section_pos["issues"] = self._help_text.index("end-1c")
        self._h("2.14 Известные ограничения и баги", "h1")

        self._h("2.14.1 Две базы OpenCode", "h2")
        self._p("На системе могут быть ДВЕ базы данных OpenCode — это НОРМАЛЬНО.")
        self._table_row(["База", "Канал", "Где используется"], header=True)
        self._table_row(["opencode.db", "latest", "Desktop-приложение (OpenCode.exe)"])
        self._table_row(["opencode-dev.db", "dev", "CLI/TUI из npm"])
        self._p("Менеджер автоопределяет БД с наибольшим количеством сессий.")
        self._p("Если кажется что сессий мало — проверьте через терминал:")
        self._code("  opencode db path  → покажет текущую БД")
        self._p("И переключитесь в менеджере на другую БД через селектор в тулбаре.")
        self._p("")

        self._h("2.14.2 Tkinter возвращает / вместо \\ (Windows)", "h2")
        self._p("tkinter.filedialog.askdirectory() может вернуть путь с forward slashes:")
        self._code("  Q:/User_Data/Desktop/project  вместо  Q:\\User_Data\\Desktop\\project")
        self._p("Это ломает колонку directory в БД → сессия не отображается в opencode.")
        self._p("Исправлено: нормализация через .replace('/', '\\\\') в двух местах:")
        self._bullet("app.py — сразу после диалога выбора папки")
        self._bullet("core.py — в методе update_session_directory")
        self._p("")

        self._h("2.14.3 Tauri Store не понимает UTF-8 BOM", "h2")
        self._p("Desktop-приложение (OpenCode.exe) хранит состояние в Tauri Store — JSON-файлах.")
        self._p("Если файл содержит UTF-8 BOM (EF BB BF = \\xEF\\xBB\\xBF) — Store падает:")
        self._code("  renderer.log: Error invoking remote method 'store-set':")
        self._code("  SyntaxError: Unexpected token '\\u00ef\\u00bb\\u00bf'")
        self._p("Причина: json.dump() с encoding='utf-8-sig' добавляет BOM в начало файла.")
        self._p("Исправление: открыть файл с encoding='utf-8' (без -sig) и пересохранить.")
        self._p("Либо удалить .dat файлы — Desktop пересоздаст их при следующем запуске.")
        self._p("")

        self._h("2.14.4 Копии БД сбивают автоопределение", "h2")
        self._p("Файлы «opencode - Копия (2).db» находятся через glob 'opencode*.db'.")
        self._p("Раньше при равенстве сессий сортировка по имени выбирала копию.")
        self._p("Исправлено: приоритет opencode.db при равном количестве сессий.")
        self._p("")

        self._h("2.14.5 Сессия не отображается — чеклист", "h2")
        self._p("По порядку:")
        self._code("  1. opencode db path          — какая БД активна?")
        self._code("  2. SELECT COUNT(*) FROM session — есть ли сессии вообще?")
        self._code("  3. SELECT id, title FROM session WHERE time_archived IS NOT NULL — архивные?")
        self._code("  4. SELECT worktree, vcs FROM project WHERE id='global' — правильный путь?")
        self._code("  5. SELECT directory FROM session WHERE directory LIKE '%/%' — есть / вместо \\?")
        self._code("  6. renderer.log              — ошибки Tauri Store (BOM)?")
        self._code("  7. opencode.global.dat       — валидный JSON?")
        self._sep()

        # ═══════════════════════════════════════════════
        # 3. MESSAGES TAB
        # ═══════════════════════════════════════════════
        self._help_section_pos["messages"] = self._help_text.index("end-1c")
        self._h("3. Вкладка «Сообщения»", "h1")

        self._h("3.1 Назначение", "h2")
        self._p("Детальный просмотр одной сессии в формате чата.")
        self._p("Позволяет увидеть, что говорил пользователь, как отвечал AI-агент,")
        self._p("какие инструменты вызывались, какие файлы менялись, какие ошибки возникли.")
        self._p("")

        self._h("3.2 Как открыть", "h2")
        self._bullet("Двойной клик по сессии на вкладке «Сессии»")
        self._bullet("Или выделить сессию и нажать «Открыть сообщения»")
        self._p("")

        self._h("3.3 Структура экрана", "h2")
        self._p("Экран разделён на две панели:")
        self._p("")
        self._b("Левая панель — список сообщений:")
        self._bullet("Каждая строка = одно сообщение (user ИЛИ assistant)")
        self._bullet("У assistant сообщения — первая часть определяет тип строки")
        self._bullet("Хронологический порядок (старые сверху / новые сверху)")
        self._p("")
        self._b("Правая панель — чат-формат:")
        self._bullet("Клик по сообщению → справа диалог в читаемом виде")
        self._bullet("Синий = ваш запрос, зелёный = ответ ассистента")
        self._bullet("Фиолетовый = инструменты, серый = рассуждения")
        self._p("")
        self._b("Сводка сверху:")
        self._p("Показывает общее количество сообщений, частей (parts) каждого типа,")
        self._p("и общий объём в мегабайтах.")
        self._code("  Сообщений: 500 | Parts: 145.2 MB | text: 2500 (12.3 MB) | ...")
        self._p("")

        self._h("3.4 Колонки списка сообщений", "h2")
        self._table_row(["Колонка", "Описание", "Формат"], header=True)
        self._table_row(["Дата и время", "Метка времени", "дд.мм.гггг чч:мм:сс"])
        self._table_row(["Тип", "Роль или тип части", "user / text / tool / reasoning / patch / step"])
        self._table_row(["Статус", "Для tool-вызовов", "completed / error / (пусто)"])
        self._table_row(["Размер", "Объём данных", "Б / КБ / МБ"])
        self._table_row(["Текст / Инструмент", "Превью", "первые 120 символов"])
        self._p("")
        self._tip("  Клик по заголовку → сортировка. Дата → перезагрузка из БД. Остальное → in-memory.")
        self._p("")

        self._h("3.5 Типы частей (parts)", "h2")
        self._table_row(["Тип", "Описание", "Цвет в чате", "Можно удалить"], header=True)
        self._table_row(["text", "Текстовый ответ модели", "Белый/чёрный", "Нет (это диалог)"])
        self._table_row(["tool", "Вызов инструмента (read/write/etc.)", "Фиолетовый (имя), зелёный/красный (статус)", "Да"])
        self._table_row(["reasoning", "Chain of Thought — рассуждения", "Серый курсив", "Да (77% БД)"])
        self._table_row(["patch", "Изменение файла", "Фиолетовый (путь)", "Да"])
        self._table_row(["step-start", "Начало шага ассистента", "Голубой разделитель", "Да"])
        self._table_row(["step-finish", "Конец шага", "Не отображается", "Да"])
        self._table_row(["compaction", "Сжатая сессия", "«(компактировано)»", "Да"])
        self._table_row(["file", "Файл как контекст", "—", "Да"])
        self._p("")

        self._h("3.6 Фильтры", "h2")
        self._p("Чекбокс «Только с контентом» — скрывает компактированные сообщения")
        self._p("(у которых нет parts). Полезно для больших сессий, где первые N сообщений")
        self._p("были сжаты и не содержат данных.")
        self._code("  Вкл → видны только сообщения с частями")
        self._code("  Размер сессии в статус-баре обновится")
        self._p("")

        self._h("3.7 Ленивая загрузка (lazy-loading)", "h2")
        self._p("Сессии могут содержать тысячи сообщений. Для производительности:")
        self._bullet("Загружается 500 сообщений за раз")
        self._bullet("При прокрутке вниз >85% — автоматически подгружаются следующие 500")
        self._bullet("Статус-бар показывает прогресс: «Загружено 2000 из 10026 сообщений»")
        self._p("")

        self._h("3.8 Массовые операции", "h2")
        self._table_row(["Кнопка", "Что делает", "Безопасность"], header=True)
        self._table_row(["Удалить выбранные", "DELETE FROM part + message по message_id", "Необратимо"])
        self._table_row(["Удалить ошибки", "DELETE FROM part WHERE status='error'", "Безопасно"])
        self._table_row(["Удалить reasoning", "DELETE FROM part WHERE type='reasoning'", "Безопасно, ~77%"])
        self._table_row(["Удалить старше 7 дн", "DELETE FROM message WHERE time_created < now-7d", "Необратимо"])
        self._table_row(["Обновить", "Перезагрузить список", "Только чтение"])
        self._p("")
        self._tip("  После удаления частей сессия пересчитывается: обновляются токены и размер.")
        self._p("")

        self._h("3.9 Пример: сократить monster-сессию", "h2")
        self._p("Проблема: 2500 сообщений, 145 МБ parts, OpenCode тормозит при загрузке.")
        self._code("  1. Откройте проблемную сессию (двойной клик)")
        self._code("  2. «Удалить reasoning» — убирает thinking-токены")
        self._code("  3. «Удалить ошибки» — убирает сломанные вызовы")
        self._code("  4. Отсортируйте по «Размеру» (▼) — самые тяжёлые сверху")
        self._code("  5. Выберите ненужные (Ctrl+Click) и «Удалить выбранные»")
        self._tip("  Результат: сессия занимает в 3-5 раз меньше места, диалог сохранён.")
        self._sep()

        # ═══════════════════════════════════════════════
        # 4. CLEANUP TAB
        # ═══════════════════════════════════════════════
        self._help_section_pos["cleanup"] = self._help_text.index("end-1c")
        self._h("4. Вкладка «Очистка»", "h1")

        self._h("4.1 Назначение", "h2")
        self._p("Пакетные операции для всей БД. Быстрое сокращение размера без ручного разбора.")
        self._p("")

        self._h("4.2 Кнопки быстрых действий", "h2")
        self._table_row(["Кнопка", "Действие", "SQL / Эффект", "Когда нужно"], header=True)
        self._table_row(["Strip Reasoning (все)", "DELETE FROM part WHERE type='reasoning'",
                         "БД ↓~77%", "Сразу после установки OpenCode"])
        self._table_row(["Strip Reasoning (выбр.)", "То же, но WHERE session_id IN (...)",
                         "Точечное удаление", "Перед удалением сессии"])
        self._table_row(["Удалить старые (>30 дн)", "DELETE FROM session WHERE time_created < now-30d",
                         "Кроме subagent", "Раз в месяц"])
        self._table_row(["Удалить subagent", "DELETE FROM session WHERE parent_id IS NOT NULL",
                         "Вложенные сессии", "Если не нужны дочерние диалоги"])
        self._table_row(["Очистить снапшоты", "rm -rf snapshot/*",
                         "Файлы версий", "После бэкапа"])
        self._table_row(["Очистить осиротевшие diff", "rm session_diff/*.json без сессии",
                         "Мусор", "После ручного удаления сессий"])
        self._table_row(["Vacuum БД", "VACUUM (SQLite)",
                         "Сжатие БД, временно ×2", "После массовых удалений"])
        self._p("")
        self._warn("  Vacuum временно удваивает размер БД! Нужно ~2× свободного места.")
        self._warn("  Перед Vacuum ОБЯЗАТЕЛЬНО закройте OpenCode — БД должна быть свободна.")
        self._p("")

        self._h("4.3 Оставить N последних", "h2")
        self._p("Поле ввода числа + кнопка «Выполнить». Удаляет все сессии кроме N самых свежих.")
        self._code("  Ввели 5 → останутся 5 последних, остальные безвозвратно удалены")
        self._warn("  Необратимо! Сначала экспортируйте нужное.")
        self._p("")

        self._h("4.4 Журнал операций", "h2")
        self._p("Внизу вкладки — лог всех выполненных операций с временной меткой.")
        self._code("  [14:23:01] Удаление reasoning из всех сессий...")
        self._code("  [14:23:15] Удалено 31547 reasoning-частей из 12 сессий")
        self._code("  [14:24:01] Vacuum...")
        self._code("  [14:24:35] База сжата: 142.1 MB → 32.3 MB")
        self._tip("  Журнал помогает оценить эффект операций.")
        self._sep()

        # ═══════════════════════════════════════════════
        # 5. DASHBOARD TAB
        # ═══════════════════════════════════════════════
        self._help_section_pos["dashboard"] = self._help_text.index("end-1c")
        self._h("5. Вкладка «Дашборд»", "h1")

        self._h("5.1 Назначение", "h2")
        self._p("Статистика хранилища OpenCode: размер БД, количество сессий, разбивка по типам.")
        self._p("Помогает принять решение: что чистить, а что оставить.")
        self._p("")

        self._h("5.2 Разделы", "h2")
        self._table_row(["Раздел", "Показывает"], header=True)
        self._table_row(["База данных", "Размер opencode.db, WAL, количество сессий/сообщений/частей"])
        self._table_row(["Файловая система", "Размер session_diff/ и snapshot/"])
        self._table_row(["График разбивки", "Визуальное соотношение: БД vs Diffs vs Снапшоты"])
        self._table_row(["Топ-10 сессий", "Самые тяжёлые сессии (клик по заголовку — сортировка)"])
        self._p("")

        self._h("5.3 Как читать", "h2")
        self._code("  DB + WAL > 500 MB       → пора чистить")
        self._code("  Reasoning занимает ~77%  → strip reasoning")
        self._code("  session_diff > 200 MB    → проверить orphan diff'ы")
        self._code("  snapshot > 100 MB        → можно очистить без потерь")
        self._code("  Пример: DB=142.1 MB, WAL=2.3 MB, Reasoning=109.5 MB, text=32.6 MB")
        self._code("  Вывод: strip reasoning сэкономит ~109 MB (77%)")
        self._tip("  Кнопка «Обновить» — пересчитать статистику.")
        self._sep()

        # ═══════════════════════════════════════════════
        # 6. STORAGE ARCHITECTURE
        # ═══════════════════════════════════════════════
        self._help_section_pos["storage"] = self._help_text.index("end-1c")
        self._h("6. Архитектура хранения данных", "h1")
        self._p("Программа работает напрямую с теми же файлами, что и OpenCode.")
        self._p("Ничего не копирует, не создаёт своих БД, не дублирует данные.")
        self._p("")

        self._h("6.1 Расположение файлов", "h2")
        self._h("Windows", "h3")
        self._code("  База:         %USERPROFILE%\\.local\\share\\opencode\\opencode.db")
        self._code("  Dev-база:      %USERPROFILE%\\.local\\share\\opencode\\opencode-dev.db")
        self._code("  WAL-журнал:   %USERPROFILE%\\.local\\share\\opencode\\opencode.db-wal")
        self._code("  Логи:          %USERPROFILE%\\.local\\share\\opencode\\log\\")
        self._code("  Diff'ы:        %USERPROFILE%\\.local\\share\\opencode\\storage\\session_diff\\")
        self._code("  Снапшоты:      %USERPROFILE%\\.local\\share\\opencode\\snapshot\\")
        self._code("  Конфиг:        %USERPROFILE%\\.opencode-manager\\config.json")
        self._code("  Desktop-сост:  %APPDATA%\\ai.opencode.desktop\\opencode.global.dat")
        self._p("")
        self._h("Linux / macOS", "h3")
        self._code("  ~/.local/share/opencode/opencode.db")
        self._code("  ~/.local/share/opencode/log/")
        self._code("  ~/.local/share/opencode/storage/session_diff/")
        self._code("  ~/.local/share/opencode/snapshot/")
        self._code("  ~/.opencode-manager/config.json")
        self._p("")

        self._h("6.2 Схема SQLite (таблицы)", "h2")
        self._p("Основные таблицы в opencode.db:")
        self._code("  session     — сессии: id, project_id, parent_id, directory, path, title")
        self._code("  message     — сообщения: id, session_id, time_created, data (JSON)")
        self._code("  part        — части: id, message_id, session_id, data (JSON)")
        self._code("  project     — проекты: id (hash), worktree, vcs, icon, time_created")
        self._code("  todo        — задачи сессии")
        self._code("  session_share — общие ссылки")
        self._code("  event       — события")
        self._code("  workspace   — workspace-состояние")
        self._p("")

        self._h("6.3 Ключевые поля session", "h2")
        self._table_row(["Поле", "Тип", "Смысл"], header=True)
        self._table_row(["id", "TEXT PK", "Уникальный ID (ses_...)"])
        self._table_row(["project_id", "TEXT FK", "ID проекта из таблицы project (или 'global')"])
        self._table_row(["parent_id", "TEXT?", "Для subagent — ID родительской сессии"])
        self._table_row(["directory", "TEXT", "Путь к проекту (backslashes на Windows)"])
        self._table_row(["path", "TEXT", "Путь с forward slashes"])
        self._table_row(["slug", "TEXT", "Короткий читаемый ID"])
        self._table_row(["title", "TEXT", "Название сессии"])
        self._table_row(["model", "TEXT", "JSON с modelID, providerID"])
        self._table_row(["time_created", "INTEGER", "Timestamp создания (ms)"])
        self._table_row(["time_updated", "INTEGER", "Timestamp последнего обновления (ms)"])
        self._table_row(["time_archived", "INTEGER?", "Timestamp архивации (NULL = активна)"])
        self._table_row(["tokens_input/output/...", "INTEGER", "Счётчики токенов"])
        self._p("")

        self._h("6.4 Система project_id (важно!)", "h2")
        self._p("project_id — хеш git-корня рабочей директории:")
        self._code("  1. opencode запускается в Q:\\...\\TestQA")
        self._code("  2. Выполняет: git rev-parse --show-toplevel → Q:/.../TestQA")
        self._code("  3. Вычисляет SHA1 хеш → a8a2d42272aeac95b2502345313a1f1866da532a")
        self._code("  4. Сохраняет в таблицу project: {id: хеш, worktree: путь}")
        self._code("  5. Все новые сессии получают этот project_id")
        self._p("")
        self._p("Старые сессии могут иметь project_id='global' — это legacy-значение.")
        self._p("Если project_id='global' и project.worktree указывает на директорию,")
        self._p("opencode всё равно показывает эти сессии для данного проекта.")
        self._p("")

        self._h("6.5 Связь между таблицами", "h2")
        self._code("  project ──1:N── session ──1:N── message ──1:N── part")
        self._code("  session ──1:N── todo")
        self._code("  session ──1:1── session_diff (внешний JSON)")
        self._p("")
        self._p("Каждая строка на вкладке «Сессии» = 1 запись session.")
        self._p("При двойном клике — читаются все message + part для этой session.")
        self._p("Session_diff — внешние JSON-файлы, не входят в БД.")
        self._p("")

        self._h("6.6 Desktop-приложение (Tauri)", "h2")
        self._p("Desktop-приложение (OpenCode.exe) версии 1.15+ — это Tauri app (Rust + web).")
        self._p("Оно НЕ ИСПОЛЬЗУЕТ opencode.db напрямую — а запускает sidecar (opencode из npm)")
        self._p("и общается с ним через HTTP API (http://127.0.0.1:56974).")
        self._p("")
        self._p("Sidecar — тот же opencode CLI, который использует opencode-dev.db.")
        self._p("Состояние UI (воркспейсы, табы, настройки) хранится в .dat файлах:")
        self._code("  %APPDATA%\\ai.opencode.desktop\\opencode.global.dat")
        self._code("  %APPDATA%\\ai.opencode.desktop\\opencode.workspace.*.dat")
        self._p("")
        self._p("Эти .dat файлы — JSON, читаемые Tauri Store. Важно: Store НЕ ПОНИМАЕТ BOM.")
        self._p("")
        self._tip("  Если Desktop не открывается или сессии пропадают — проверьте renderer.log")
        self._code("  %APPDATA%\\ai.opencode.desktop\\logs\\*\\renderer.log → ошибки 'store-set'")
        self._sep()

        # ═══════════════════════════════════════════════
        # 7. MENUS
        # ═══════════════════════════════════════════════
        self._help_section_pos["menu"] = self._help_text.index("end-1c")
        self._h("7. Строка меню", "h1")

        self._h("7.1 Файл", "h2")
        self._table_row(["Команда", "Клавиша", "Действие"], header=True)
        self._table_row(["Обновить (F5)", "F5", "Перезагрузить список сессий"])
        self._table_row(["Выход", "—", "Закрыть программу"])
        self._p("")

        self._h("7.2 Инструменты", "h2")
        self._table_row(["Команда", "Действие", "Когда нужно"], header=True)
        self._table_row(["Vacuum БД", "VACUUM — пересборка SQLite", "После массовых удалений"])
        self._table_row(["Очистить снапшоты", "Удалить snapshot/*", "После бэкапа"])
        self._table_row(["Очистить осиротевшие diff", "Удалить JSON без сессии", "Если мусор > 100 MB"])
        self._table_row(["Удалить reasoning (все)", "Strip reasoning из ВСЕХ сессий", "При установке"])
        self._table_row(["Переключить тему", "Тёмная ↔ Светлая", "По желанию"])
        self._p("")
        self._tip("  Тема запоминается в ~/.opencode-manager/config.json и восстанавливается при запуске.")
        self._p("")

        self._h("7.3 Справка", "h2")
        self._table_row(["Команда", "Действие"], header=True)
        self._table_row(["О программе", "Версия, контакты"])
        self._sep()

        # ═══════════════════════════════════════════════════
        # 8. DB SELECTOR (DETAILED)
        # ═══════════════════════════════════════════════════
        self._help_section_pos['db_selector_detailed'] = self._help_text.index('end-1c')
        self._h('8. Выбор базы данных (DB Selector)', 'h1')
        self._h('8.1 Обзор и назначение', 'h2')
        self._p('Менеджер поддерживает работу с несколькими SQLite-базами OpenCode одновременно. Выпадающий список в тулбаре вкладки «Сессии» позволяет переключаться между ними на лету, а алгоритм автоопределения находит все доступные базы в стандартной директории и выбирает наиболее подходящую.')
        self._p('Пользователь может иметь несколько баз данных по разным причинам: установлены разные каналы (stable + dev), есть системные копии Windows Shadow Copy, созданы ручные бэкапы, или остались данные от предыдущих версий opencode. Менеджер показывает все найденные базы и даёт пользователю выбор.')
        self._h('Где хранятся базы данных', 'h2')
        self._p('По умолчанию opencode хранит базы данных в директории:')
        self._code("~/.local/share/opencode/")
        self._p('На Windows это соответствует:')
        self._code("C:\\Users\\<User>\\.local\\share\\opencode\\")
        self._p('В этой директории находятся файлы opencode.db (основная база), opencode-dev.db (dev-сборка), а также копии, созданные системой или пользователем вручную.')
        self._h('8.2 Алгоритм автоопределения', 'h2')
        self._h('Шаг 1: сканирование директории', 'h3')
        self._p('Метод list_databases() сканирует директорию на наличие файлов, соответствующих маске opencode*.db:')
        self._code('@staticmethod')
        self._code('def list_databases() -> list[tuple[str, str, int]]:')
        self._code("    base = Path.home() / '.local' / 'share' / 'opencode'")
        self._code('    for f in base.glob("opencode*.db"):')
        self._code("        if name.endswith('.db-shm') or name.endswith('.db-wal'):")
        self._code('            continue')
        self._code("        if name.endswith('.backup-'):")
        self._code('            continue')
        self._code('        conn = sqlite3.connect(str(f), timeout=10)')
        self._code("        count = conn.execute('SELECT COUNT(*) FROM session').fetchone()[0]")
        self._code('        candidates.append((str(f), label, count))')
        self._code('    candidates.sort(key=lambda x: x[2], reverse=True)')
        self._code('    return candidates')
        self._p('Алгоритм выполняет следующие шаги:')
        self._bullet('Поиск всех файлов, соответствующих маске opencode*.db')
        self._bullet('Фильтрация служебных файлов .db-shm и .db-wal (WAL-журналы)')
        self._bullet('Фильтрация файлов с суффиксом .backup- (служебные копии SQLite)')
        self._bullet('Подключение к каждому файлу с таймаутом 10 секунд (для обхода WAL-блокировок)')
        self._bullet('Подсчёт количества сессий в каждой базе')
        self._bullet('Сортировка по убыванию числа сессий')
        self._warn('WAL-файлы могут блокировать чтение, если Desktop-приложение запущено. Таймаут 10 секунд позволяет дождаться освобождения блокировки.')
        self._h('Шаг 2: выбор основной БД', 'h3')
        self._p('Метод _detect_db_path() выбирает базу с максимальным числом сессий:')
        self._code('def _detect_db_path() -> Optional[str]:')
        self._code('    dbs = list_databases()')
        self._code('    if dbs:')
        self._code("        return dbs[0][0]  # БД с максимальным числом сессий")
        self._p('При равном количестве сессий действуют следующие правила:')
        self._bullet('Приоритет у opencode.db (как основной базы)')
        self._bullet('Если opencode.db отсутствует — выбирается первая по алфавиту')
        self._p('Если баз данных не найдено — возвращается None, и менеджер предлагает пользователю указать путь вручную.')
        self._h('8.3 Переключение между базами', 'h2')
        self._p('В тулбаре вкладки «Сессии» находится выпадающий список (Combobox) со списком всех найденных баз данных и количеством сессий в каждой:')
        self._code('[opencode (60 сес.)] [Сортировка...] [Поиск...] [Sub] [Обновить]')
        self._p('При выборе другой базы из списка вызывается метод _switch_db():')
        self._code('def _switch_db(self, idx: int):')
        self._code('    path, label, _ = self._db_list[idx]')
        self._code('    self.db = OpenCodeDB(path)')
        self._code('    self.cli = OpenCodeCLI(db_path=path)')
        self._code('    self._load_sessions()')
        self._code('    self.status_var.set(f"БД: {label} ({path})")')
        self._p('После переключения:')
        self._bullet('Создаётся новое подключение к выбранной базе')
        self._bullet('Переинициализируется CLI-интерфейс с новым путём')
        self._bullet('Перезагружается список сессий')
        self._bullet('Обновляется статус-бар с именем и путём активной БД')
        self._tip('При переключении все текущие фильтры и поиск сбрасываются. Рекомендуется перед переключением сохранить важные изменения.')
        self._h('8.4 Типы баз данных', 'h2')
        self._p('В таблице ниже приведены типичные базы данных, которые могут быть найдены:')
        self._table_row(['Имя в списке', 'Файл', 'Сессий', 'Происхождение'], header=True)
        self._table_row(['opencode', 'opencode.db', '56-60', 'Desktop / stable CLI'])
        self._table_row(['opencode-dev', 'opencode-dev.db', '3-7', 'Dev-сборка npm run dev'])
        self._table_row(['opencode1', 'opencode1.db', '~51', 'Ручная копия'])
        self._table_row(['opencode - копия', 'opencode - копия.db', '~51', 'Windows Shadow Copy'])
        self._table_row(['opencode - копия (2)', 'opencode - копия (2).db', '~51', 'Ещё одна копия'])
        self._p('Менеджер не фильтрует найденные базы — все отображаются в списке. Пользователь сам выбирает нужную.')
        self._h('8.5 Статус-бар', 'h2')
        self._p('Активная база данных отображается в статус-баре:')
        self._code('[opencode] Загружено 60 сессий  |  БД: opencode (60 сес.)')
        self._p('Статус-бар показывает:')
        self._bullet('Имя текущей базы (label) с количеством сессий')
        self._bullet('Полный путь к файлу базы данных (в скобках)')
        self._bullet('Общее количество загруженных сессий')
        self._h('8.6 Примеры использования', 'h2')
        self._h('Я не вижу свои сессии', 'h3')
        self._p('Если вы не видите привычных сессий, выполните следующие шаги:')
        self._bullet('Проверьте статус-бар: какая база данных активна?')
        self._bullet('Если активна opencode-dev — переключитесь на opencode')
        self._bullet('Если opencode не отображается — проверьте её наличие в терминале:')
        self._code('opencode db path')
        self._bullet('Если база существует, но не найдена — нажмите «Обновить» для повторного сканирования')
        self._h('Перенос сессии из dev в stable', 'h3')
        self._p('Чтобы перенести сессию из dev-сборки в основную базу:')
        self._bullet('Выберите opencode-dev в селекторе баз')
        self._bullet('Найдите нужную сессию и экспортируйте её в JSON')
        self._bullet('Переключитесь на opencode (основная база)')
        self._bullet('Импортируйте сохранённый JSON-файл')
        self._h('8.7 Особенности Windows', 'h2')
        self._h('PowerShell-обёртка', 'h3')
        self._p('На Windows opencode представляет собой .ps1-скрипт, а не .exe-файл. Из-за этого subprocess.run("opencode") завершается с FileNotFoundError.')
        self._p('Решение: вызывать через PowerShell:')
        self._code('subprocess.run(["powershell", "-NoProfile", "-Command", "opencode db path"])')
        self._warn('Перед запуском убедитесь, что opencode установлен как команда PowerShell (доступен в $env:PATH). В противном случае укажите полный путь к скрипту.')
        self._h('Shadow Copy (теневые копии)', 'h3')
        self._p('Windows может создавать автоматические копии файлов через механизм Shadow Copy. Такие копии попадают в директорию opencode и обнаруживаются сканером как отдельные базы данных:')
        self._code('opencode - копия.db')
        self._code('opencode - копия (2).db')
        self._p('Менеджер не скрывает такие копии — они отображаются в списке, чтобы пользователь мог явно выбрать нужную базу. Рекомендуется удалять ненужные копии вручную.')
        self._h('Desktop overwrite', 'h3')
        self._p('Если Desktop-приложение opencode запущено, оно может перезаписать изменения в global.dat при своём закрытии. Это касается поля globalSync.project.')
        self._tip('Перед редактированием настроек проекта в менеджере закройте Desktop-приложение opencode, чтобы избежать конфликта при синхронизации.')
        self._h('8.8 SQL-запросы для диагностики', 'h2')
        self._p('Следующие запросы помогут диагностировать состояние баз данных:')
        self._code("SELECT 'opencode.db' AS db, COUNT(*) AS sessions FROM session")
        self._code("UNION ALL SELECT 'opencode-dev.db', COUNT(*) FROM main.session")
        self._p('Эти запросы можно выполнить через любой SQLite-клиент или встроенную диагностику менеджера.')
        self._h('8.9 Известные ограничения', 'h2')
        self._h('WAL-блокировка при запущенном Desktop', 'h3')
        self._p('Если Desktop-приложение opencode запущено, WAL-файлы (.db-wal, .db-shm) могут блокировать чтение базы данных. Менеджер использует timeout=10 при подключении, но при долгой блокировке подключение может не состояться.')
        self._warn('Рекомендуется закрывать Desktop-приложение opencode при работе с менеджером, особенно для операций записи.')
        self._h('Отсутствие фильтрации копий', 'h3')
        self._p('Менеджер не фильтрует найденные базы — все файлы, соответствующие маске opencode*.db, отображаются в списке. Это может привести к загромождению списка, если в директории много теневых копий или бэкапов.')
        self._tip('Периодически очищайте директорию ~/.local/share/opencode/ от ненужных копий и бэкапов, чтобы список баз оставался компактным.')
        self._h('Нет автообновления списка БД', 'h3')
        self._p('Список баз данных сканируется только при запуске менеджера и при нажатии кнопки «Обновить». Если новая база появилась во время работы, она не отобразится до ручного обновления.')
        self._sep()

        # ═══════════════════════════════════════════════════
        # 9. FULL DB SCHEMA
        # ═══════════════════════════════════════════════════
        self._help_section_pos["schema_detailed"] = self._help_text.index("end-1c")
        self._h("9. Полная схема данных SQLite", "h1")
        self._p("Полное описание всех таблиц, полей, связей и правил работы с данными OpenCode.")
        self._p("Актуально для opencode.db и opencode-dev.db.")

        self._h("9.1 Обзор таблиц", "h2")
        self._p("В БД OpenCode 7 таблиц:")
        self._table_row(["Таблица", "Назначение", "Ключевая связь"], header=True)
        self._table_row(["project", "Проекты (git-репозитории)", "session.project_id → project.id"])
        self._table_row(["session", "Сессии (диалоги)", "message.session_id → session.id"])
        self._table_row(["message", "Сообщения (user/assistant)", "part.message_id → message.id"])
        self._table_row(["part", "Части сообщений (tool, reasoning, ...)", "part.session_id → session.id"])
        self._table_row(["todo", "Задачи сессии", "todo.session_id → session.id"])
        self._table_row(["session_share", "Общие ссылки", "session_share.session_id → session.id"])
        self._table_row(["event", "События ЖЦ", "event.session_id → session.id"])
        self._table_row(["workspace", "Workspace-состояние Desktop", "локальные .dat файлы"])

        self._h("9.2 Таблица project", "h2")
        self._p("Зарегистрированные git-репозитории, где запускался OpenCode.")
        self._table_row(["Поле", "Тип", "Ограничения", "Описание"], header=True)
        self._table_row(["id", "TEXT", "PK", "SHA1 хеш git-корня (lowercased FSlash). 'global' для legacy"])
        self._table_row(["worktree", "TEXT", "NOT NULL", "Абсолютный путь к корню репозитория"])
        self._table_row(["vcs", "TEXT", "", "'git' или NULL"])
        self._table_row(["name", "TEXT", "", "Название проекта (может быть NULL)"])
        self._table_row(["icon_url", "TEXT", "", "Base64-иконка"])
        self._table_row(["time_created", "INTEGER", "NOT NULL", "Когда проект впервые обнаружен (ms)"])
        self._table_row(["time_updated", "INTEGER", "NOT NULL", "Последнее обновление (ms)"])
        self._b("Алгоритм project_id:")
        self._code("  1. git rev-parse --show-toplevel → Q:/User_Data/Desktop/TestQA")
        self._code("  2. lowercased → q:/user_data/desktop/testqa")
        self._code("  3. SHA1(forward-slash путь) → a8a2d42272aeac95b2502345313a1f1866da532a")
        self._code("  4. INSERT INTO project (id, worktree, ...)")
        self._warn("  В разных версиях OpenCode алгоритм хеширования может отличаться.")

        self._h("9.3 Таблица session", "h2")
        self._p("Главная таблица. Одна запись = один диалог.")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "Уникальный ID вида ses_<random>."])
        self._table_row(["project_id", "TEXT FK NOT NULL", "ID из project или 'global'."])
        self._table_row(["parent_id", "TEXT?", "Для subagent — ID родителя. NULL = корневая."])
        self._table_row(["slug", "TEXT NOT NULL", "Короткий kebab-case ID (quick-guide). Уникален в рамках project."])
        self._table_row(["directory", "TEXT NOT NULL", "Путь ОС (на Windows с backslashes)."])
        self._table_row(["path", "TEXT?", "Тот же путь с forward slashes."])
        self._table_row(["title", "TEXT NOT NULL", "Название (первый промпт или авто)."])
        self._table_row(["version", "TEXT NOT NULL", "Формат. Всегда 'local'."])
        self._table_row(["model", "TEXT?", "JSON: {modelID, providerID, variant}."])
        self._table_row(["agent", "TEXT?", "'build', 'explore', 'general' или NULL."])
        self._table_row(["time_created", "INTEGER NOT NULL", "Timestamp (ms)."])
        self._table_row(["time_updated", "INTEGER NOT NULL", "Последнее изменение (ms)."])
        self._table_row(["time_compacting", "INTEGER?", "Последняя компактация (ms)."])
        self._table_row(["time_archived", "INTEGER?", "Архивация (ms). NULL = активна."])
        self._table_row(["tokens_input", "INTEGER", "Входящие токены."])
        self._table_row(["tokens_output", "INTEGER", "Исходящие токены."])
        self._table_row(["tokens_reasoning", "INTEGER", "Reasoning-токены."])
        self._table_row(["tokens_cache_read", "INTEGER", "Из кэша."])
        self._table_row(["tokens_cache_write", "INTEGER", "В кэш."])
        self._table_row(["cost", "REAL", "Стоимость в $."])
        self._table_row(["metadata", "TEXT?", "JSON-метаданные."])
        self._h("Типы сессий", "h3")
        self._table_row(["Тип", "parent_id", "project_id", "Описание"], header=True)
        self._table_row(["Корневая", "NULL", "хеш/'global'", "Создана пользователем. В списке сессий."])
        self._table_row(["Subagent", "ID родителя", "как у родителя", "Создана @explore/@general. Скрыта."])
        self._table_row(["Orphan", "ID удалённого", "любой", "Сирота, родитель удалён."])
        self._table_row(["Архивная", "любой", "любой", "time_archived IS NOT NULL. Скрыта."])

        self._h("9.4 Таблица message", "h2")
        self._p("Сообщения сессии. Каждое = один обмен user/assistant.")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "msg_<random>."])
        self._table_row(["session_id", "TEXT FK NOT NULL", "→ session.id."])
        self._table_row(["time_created", "INTEGER NOT NULL", "Timestamp (ms). Определяет порядок."])
        self._table_row(["time_updated", "INTEGER NOT NULL", "Последнее изменение."])
        self._table_row(["data", "TEXT (JSON)", "Содержимое. Поля: role, summary.diffs."])
        self._p("JSON-структура data:")
        self._code('  {')
        self._code('    "role": "user" | "assistant",')
        self._code('    "summary": {')
        self._code('      "diffs": [{"file":"src/index.ts","additions":10,"deletions":2}]')
        self._code('    }')
        self._code('  }')

        self._h("9.5 Таблица part", "h2")
        self._p("Части сообщений. Одно сообщение = много частей. Самая тяжёлая таблица (~95% размера БД).")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "prt_<random>."])
        self._table_row(["message_id", "TEXT FK NOT NULL", "→ message.id."])
        self._table_row(["session_id", "TEXT FK NOT NULL", "→ session.id (денормализация)."])
        self._table_row(["time_created", "INTEGER NOT NULL", "Timestamp (ms)."])
        self._table_row(["time_updated", "INTEGER NOT NULL", "Последнее изменение."])
        self._table_row(["data", "TEXT (JSON)", "Содержимое. Тип = поле type внутри JSON."])
        self._h("Типы part (поле type в data)", "h3")
        self._table_row(["Тип", "Назначение", "Размер"], header=True)
        self._table_row(["text", "Текстовый ответ модели", "Умеренно"])
        self._table_row(["tool", "Вызов bash/read/edit", "Много (output)"])
        self._table_row(["reasoning", "Chain of Thought", "Очень много (~77%)"])
        self._table_row(["patch", "Изменение файла", "Много"])
        self._table_row(["step-start", "Начало шага", "Минимум"])
        self._table_row(["step-finish", "Конец шага", "Минимум"])
        self._table_row(["compaction", "Сжатая сессия", "Минимум"])
        self._table_row(["file", "Файл-контекст", "Много"])
        self._table_row(["agent", "Агентское сообщение", "Минимум"])
        self._table_row(["retry", "Повтор вызова", "Минимум"])
        self._h("Примеры JSON для разных типов", "h3")
        self._b("reasoning:")
        self._code('  {"type":"reasoning","text":"...","time":{"start":...,"end":...}}')
        self._b("tool:")
        self._code('  {"type":"tool","tool":"bash","callID":"...","state":{"input":...,"output":...}}')
        self._b("patch:")
        self._code('  {"type":"patch","tool":"edit","filePath":"src/index.ts","state":{"input":{...}}}')

        self._h("9.6 Таблица todo", "h2")
        self._p("Задачи, созданные внутри сессии через todo-механизм.")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "Уникальный ID задачи."])
        self._table_row(["session_id", "TEXT FK", "→ session.id."])
        self._table_row(["content", "TEXT", "Текст задачи."])
        self._table_row(["status", "TEXT", "'pending', 'completed', 'cancelled'."])
        self._table_row(["priority", "TEXT", "'high', 'medium', 'low'."])
        self._table_row(["time_created", "INTEGER", "Timestamp (ms)."])

        self._h("9.7 Таблица session_share", "h2")
        self._p("Общие ссылки на сессии (share-функционал OpenCode).")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "Уникальный ID ссылки."])
        self._table_row(["session_id", "TEXT FK", "→ session.id."])
        self._table_row(["token", "TEXT", "Токен доступа."])
        self._table_row(["time_created", "INTEGER", "Timestamp создания (ms)."])
        self._table_row(["expires_at", "INTEGER?", "Истечение (ms). NULL = бессрочно."])

        self._h("9.8 Таблица event", "h2")
        self._p("События жизненного цикла сессий.")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "INTEGER PK AUTOINCREMENT", "Уникальный ID."])
        self._table_row(["session_id", "TEXT", "→ session.id (если применимо)."])
        self._table_row(["type", "TEXT", "'session_created', '_updated', '_archived', '_deleted'."])
        self._table_row(["data", "TEXT (JSON)", "Доп. данные."])
        self._table_row(["time_created", "INTEGER", "Timestamp (ms)."])

        self._h("9.9 Таблица workspace", "h2")
        self._p("Состояние workspace'ов Desktop-приложения (Tauri).")
        self._table_row(["Поле", "Тип", "Описание"], header=True)
        self._table_row(["id", "TEXT PK", "Уникальный ID workspace."])
        self._table_row(["name", "TEXT", "Название."])
        self._table_row(["state", "TEXT (JSON)", "JSON: табы, панели, позиции."])
        self._table_row(["time_created", "INTEGER", "Timestamp (ms)."])
        self._table_row(["time_updated", "INTEGER", "Последнее изменение (ms)."])
        self._tip("  Workspace также дублируется в .dat файлах Desktop:")

        self._h("9.10 CREATE TABLE (SQL-определения)", "h2")
        self._p("Полные DDL основных таблиц:")
        self._h("session", "h3")
        self._code("CREATE TABLE session (")
        self._code("  id               TEXT  PRIMARY KEY,")
        self._code("  project_id       TEXT  NOT NULL  REFERENCES project(id),")
        self._code("  parent_id        TEXT,")
        self._code("  slug             TEXT  NOT NULL,")
        self._code("  directory        TEXT  NOT NULL,")
        self._code("  path             TEXT,")
        self._code("  title            TEXT  NOT NULL,")
        self._code("  version          TEXT  NOT NULL  DEFAULT 'local',")
        self._code("  model            TEXT,")
        self._code("  agent            TEXT,")
        self._code("  time_created     INTEGER NOT NULL,")
        self._code("  time_updated     INTEGER NOT NULL,")
        self._code("  time_compacting  INTEGER,")
        self._code("  time_archived    INTEGER,")
        self._code("  tokens_input     INTEGER DEFAULT 0,")
        self._code("  tokens_output    INTEGER DEFAULT 0,")
        self._code("  tokens_reasoning INTEGER DEFAULT 0,")
        self._code("  tokens_cache_read  INTEGER DEFAULT 0,")
        self._code("  tokens_cache_write INTEGER DEFAULT 0,")
        self._code("  cost             REAL    DEFAULT 0.0,")
        self._code("  metadata         TEXT")
        self._code(");")
        self._h("message", "h3")
        self._code("CREATE TABLE message (")
        self._code("  id             TEXT  PRIMARY KEY,")
        self._code("  session_id     TEXT  NOT NULL  REFERENCES session(id),")
        self._code("  time_created   INTEGER NOT NULL,")
        self._code("  time_updated   INTEGER NOT NULL,")
        self._code("  data           TEXT")
        self._code(");")
        self._h("part", "h3")
        self._code("CREATE TABLE part (")
        self._code("  id             TEXT  PRIMARY KEY,")
        self._code("  message_id     TEXT  NOT NULL  REFERENCES message(id),")
        self._code("  session_id     TEXT  NOT NULL  REFERENCES session(id),")
        self._code("  time_created   INTEGER NOT NULL,")
        self._code("  time_updated   INTEGER NOT NULL,")
        self._code("  data           TEXT")
        self._code(");")
        self._h("project", "h3")
        self._code("CREATE TABLE project (")
        self._code("  id             TEXT  PRIMARY KEY,")
        self._code("  worktree       TEXT  NOT NULL,")
        self._code("  vcs            TEXT,")
        self._code("  name           TEXT,")
        self._code("  icon_url       TEXT,")
        self._code("  time_created   INTEGER NOT NULL,")
        self._code("  time_updated   INTEGER NOT NULL")
        self._code(");")

        self._h("9.11 ER-диаграмма (связи таблиц)", "h2")
        self._p("Полная схема отношений:")
        self._code("  project  ──1:N── session  ──1:N── message  ──1:N── part")
        self._code("    │                    │")
        self._code("    │                    ├──1:N── todo")
        self._code("    │                    ├──1:N── session_share")
        self._code("    │                    └──1:N── event")
        self._p("Денормализация: part.session_id дублирует session.id для производительности. Это позволяет считать размер сессии без JOIN:")
        self._code("  SELECT session_id, COUNT(*), SUM(LENGTH(data))")
        self._code("  FROM part GROUP BY session_id;")
        self._p("Внешние файлы (не входят в БД):")
        self._code("  session_diff/<session_id>.json       — 1:1 с session (undo/redo)")
        self._code("  snapshot/<project_id>/<slug>.json   — снапшот компактации")

        self._h("9.12 Правила форматирования полей", "h2")
        self._b("directory vs path:")
        self._p("  directory — системный формат (Windows: C:\\Users\\...\\project)")
        self._p("  path      — forward slashes (C:/Users/.../project)")
        self._b("project_id resolution:")
        self._code("  1. git rev-parse --show-toplevel → корень репозитория")
        self._code("  2. Привести к forward slashes, lowercased")
        self._code("  3. SHA1 хеш → project.id")
        self._code("  4. Если git не найден → project_id = 'global'")
        self._b("ID format:")
        self._code("  session: ses_<random>        (напр. ses_AbCdEf1234)")
        self._code("  message: msg_<random>        (напр. msg_XyZ789AbC1)")
        self._code("  part:    prt_<random>        (напр. prt_QwErTy5678)")
        self._code("  project: SHA1 (40 hex)       (напр. a8a2d42272ae...)")
        self._b("slug format:")
        self._p("  kebab-case, уникален в рамках project_id.")

        self._h("9.13 Жизненный цикл сессии", "h2")
        self._p("Сессия проходит 5 стадий:")
        self._b("1. Создание (CREATE)")
        self._code("  INSERT INTO session (id, project_id, slug, directory, title, ...)")
        self._code("  title = первый промпт пользователя")
        self._b("2. Обновление (UPDATE)")
        self._code("  INSERT INTO message + part — новые сообщения")
        self._code("  UPDATE session SET time_updated = ?, tokens_* = ?, cost = ?")
        self._b("3. Компактация (COMPACT)")
        self._code("  Старые сообщения сжимаются в одно compaction-part")
        self._code("  Создаётся snapshot/: project_id/slug.json (полный слепок)")
        self._code("  DELETE FROM part WHERE session_id = ? (старые части)")
        self._b("4. Архивация (ARCHIVE)")
        self._code("  UPDATE session SET time_archived = ? WHERE id = ?")
        self._code("  Сессия скрывается из списка OpenCode, но данные сохраняются")
        self._b("5. Удаление (DELETE)")
        self._code("  DELETE FROM part WHERE session_id = ?")
        self._code("  DELETE FROM message WHERE session_id = ?")
        self._code("  DELETE FROM session WHERE id = ?")
        self._tip("  Архивация безопаснее удаления: всегда можно разархивировать.")
        self._warn("  Удаление необратимо! Session_diff тоже удаляется.")

        self._h("9.14 Дерево subagent'ов", "h2")
        self._p("Subagent'ы — дочерние сессии @explore/@general внутри корневой.")
        self._p("Структура дерева:")
        self._code("  Корневая (parent_id = NULL)")
        self._code("    ├── Subagent #1 (parent_id = ID_корневой)")
        self._code("    │     ├── Subagent #1.1")
        self._code("    │     └── Subagent #1.2")
        self._code("    ├── Subagent #2")
        self._code("    └── Subagent #3")
        self._b("Правила:")
        self._code("  • parent_id = NULL  → корневая сессия")
        self._code("  • parent_id = <id>  → subagent, наследующий project_id от родителя")
        self._code("  • Subagent'ы скрыты из общего списка сессий OpenCode")
        self._code("  • При удалении родителя → orphan (сирота)")
        self._b("Поиск orphan:")
        self._code("  SELECT * FROM session WHERE parent_id IS NOT NULL")
        self._code("    AND parent_id NOT IN (SELECT id FROM session);")
        self._b("Поиск всех subagent'ов для сессии:")
        self._code("  WITH RECURSIVE tree AS (")
        self._code("    SELECT id, parent_id, title, 0 AS level FROM session WHERE id = ?")
        self._code("    UNION ALL")
        self._code("    SELECT s.id, s.parent_id, s.title, t.level + 1")
        self._code("    FROM session s JOIN tree t ON s.parent_id = t.id")
        self._code("  ) SELECT * FROM tree ORDER BY level;")
        self._warn("  Orphan-сессии не видны в UI, но занимают место!")
        self._sep()

        # ═══════════════════════════════════════════════════
        # 10. MCP-инспектор баз opencode
        # ═══════════════════════════════════════════════════
        self._help_section_pos["mcp_detailed"] = self._help_text.index("end-1c")
        self._h("10. MCP-инспектор баз opencode", "h1")
        self._p("MCP (Model Context Protocol) — стандартный протокол opencode для подключения внешних инструментов к LLM-агенту. Сервер mcp-opencode-db.py предоставляет прямой доступ к SQLite-базам opencode прямо изнутри opencode-сессии.")
        self._p("Зачем это нужно: AI-агент получает возможность самостоятельно исследовать структуру БД, находить orphan-сессии, сравнивать базы и выполнять read-only SQL-запросы без участия пользователя. Это ускоряет диагностику и автоматизирует рутинные проверки целостности данных.")

        self._h("10.1 Инструмент oc_list_dbs", "h2")
        self._b("Параметры: нет")
        self._b("Возвращает:")
        self._p("JSON-массив объектов с полями: path (путь к файлу БД), name (имя файла), sessions (количество сессий), size (размер файла в байтах).")
        self._b("Пример ответа:")
        self._code('  [{"path":"C:\\Users\\.local\\share\\opencode\\opencode.db","name":"opencode.db","sessions":60,"size":192495616}]')
        self._p("Запросите «покажи все базы opencode» — агент выполнит oc_list_dbs и выведет таблицу с найденными БД, числом сессий и размером.")

        self._h("10.2 Инструмент oc_list_sessions", "h2")
        self._b("Параметры:")
        self._table_row(["Параметр", "Тип", "Обяз.", "Описание"], header=True)
        self._table_row(["db", "string", "да", "Имя или путь к БД (opencode.db / opencode-dev.db)"])
        self._table_row(["project_id", "string", "нет", "Фильтр по точному совпадению project_id"])
        self._table_row(["parent_id", "string", "нет", "\"null\" — корневые, \"any\" — дети, \"<ID>\" — дети конкретного родителя"])
        self._table_row(["search", "string", "нет", "LIKE-поиск по заголовку и ID сессии"])
        self._table_row(["limit", "integer", "нет", "Максимум результатов (по умолчанию 100)"])
        self._b("Возвращает: JSON-массив сессий с полями: id, title, created_at, updated_at, project_id, parent_id, child_count, message_count.")
        self._p("«Найди сессии с ошибками в opencode-dev.db» → oc_list_sessions(db=\"opencode-dev.db\", search=\"error\")")
        self._p("«Покажи корневые сессии» → parent_id=\"null\"")

        self._h("10.3 Инструмент oc_get_session", "h2")
        self._b("Параметры:")
        self._table_row(["Параметр", "Тип", "Обяз.", "Описание"], header=True)
        self._table_row(["db", "string", "да", "Имя или путь к БД"])
        self._table_row(["session_id", "string", "да", "ID сессии (ses_...) для загрузки"])
        self._b("Возвращает:")
        self._p("Полный объект сессии: метаданные (id, title, created_at, updated_at), все сообщения (messages) с ролями user/assistant, а также parts.")
        self._warn("ВНИМАНИЕ: сессии с 1000+ сообщений могут вернуть мегабайты данных!")
        self._p("Рекомендуется сначала использовать oc_list_sessions с фильтрацией.")

        self._h("10.4 Инструмент oc_get_children", "h2")
        self._b("Параметры:")
        self._table_row(["Параметр", "Тип", "Обяз.", "Описание"], header=True)
        self._table_row(["db", "string", "да", "Имя или путь к БД"])
        self._table_row(["parent_id", "string", "да", "ID родительской сессии"])
        self._table_row(["recursive", "bool", "нет", "Рекурсивный обход (по умолчанию true)"])
        self._b("Возвращает:")
        self._p("Дочерние сессии с полями: id, title, parent_id, depth, child_count. При recursive=true обход до глубины 10 уровней.")

        self._h("10.5 Инструмент oc_check_orphans", "h2")
        self._b("Параметры: нет")
        self._b("Возвращает:")
        self._p("Объект сравнения opencode.db vs opencode-dev.db: статистика каждой БД, orphan-дети, project_id mismatch, сессии уникальные для каждой БД.")
        self._p("«Проверь целостность БД» → агент выполнит oc_check_orphans и покажет сводку.")

        self._h("10.6 Инструмент oc_query", "h2")
        self._b("Параметры:")
        self._table_row(["Параметр", "Тип", "Обяз.", "Описание"], header=True)
        self._table_row(["db", "string", "да", "Имя или путь к БД"])
        self._table_row(["sql", "string", "да", "Read-only SQL-запрос (только SELECT)"])
        self._b("Возвращает: результат SELECT-запроса в виде JSON-массива строк.")
        self._warn("ОГРАНИЧЕНИЕ: только SELECT. INSERT, UPDATE, DELETE, DROP, ALTER, CREATE, ATTACH, DETACH, PRAGMA write — отклоняются.")
        self._p("«Сколько сессий в каждой БД?» → oc_query(db=\"opencode.db\", sql=\"SELECT COUNT(*) as cnt FROM session\")")

        self._h("10.7 Подключение к opencode", "h2")
        self._b("Файл конфига: C:\\Users\\.config\\opencode\\opencode.jsonc")
        self._code("  \"mcp-opencode-db\": {")
        self._code("    \"type\": \"local\",")
        self._code("    \"command\": [\"C:\\\\Users\\\\...\\\\python.exe\", \"Q:\\\\...\\\\mcp-opencode-db.py\"],")
        self._code("    \"enabled\": true")
        self._code("  }")
        self._tip("На Windows обязательно указывайте ПОЛНЫЙ путь к python.exe. Запись \"python\" не работает — opencode не видит системный PATH.")
        self._warn("После изменения opencode.jsonc требуется ПОЛНЫЙ ПЕРЕЗАПУСК opencode. MCP-серверы инициализируются один раз при старте.")

        self._h("10.8 Безопасность", "h2")
        self._b("Только чтение:")
        self._p("Ни один инструмент не изменяет данные в БД. Запись сознательно не реализована — риск повреждения данных через AI-агента слишком высок.")
        self._b("SQL-валидация:")
        self._p("Инструмент oc_query принимает только SELECT-запросы. Любая попытка выполнить INSERT, UPDATE, DELETE, DROP, ALTER будет отклонена на уровне парсера SQL.")
        self._b("Изоляция данных:")
        self._p("MCP-сервер не имеет доступа к файловой системе за пределами директорий баз opencode.")

        self._h("10.9 Диагностика MCP", "h2")
        self._b("1. Проверка конфига:")
        self._code("  opencode debug config | grep -A5 mcp-opencode-db")
        self._b("2. Проверка установки пакета mcp:")
        self._code("  python -c \"import mcp; print('OK')\"")
        self._b("3. Запуск сервера напрямую:")
        self._code("  python Q:\\...\\mcp-opencode-db.py")
        self._p("Сервер должен запуститься без ошибок и перейти в режим ожидания stdio.")
        self._b("4. Проверка путей в opencode.jsonc:")
        self._p("Убедитесь, что пути к python.exe и mcp-opencode-db.py абсолютные. На Windows пути с пробелами должны быть экранированы.")
        self._b("5. Логи opencode:")
        self._p("Откройте Developer Tools (Ctrl+Shift+I) и проверьте вкладку Console. Ошибки MCP логируются с префиксом [mcp].")

        self._h("10.10 Ограничения", "h2")
        self._b("MCP in-session: нельзя добавить или перезагрузить без перезапуска opencode. Все MCP-инструменты инициализируются один раз при старте.")
        self._b("Блокировка WAL на Windows: если Desktop держит WAL-блокировку, запрос может упасть по таймауту. Решение: закрыть Desktop или подождать.")
        self._b("Большие сессии: oc_get_session для 1000+ сообщений возвращает мегабайты данных. Используйте фильтрацию через oc_list_sessions.")
        self._b("Только локальные базы: сервер не поддерживает удалённые БД.")
        self._sep()

        # ═══════════════════════════════════════════════════
        # 11. SQL-запросы для диагностики (расширенные)
        # ═══════════════════════════════════════════════════
        self._help_section_pos["sql_ref_detailed"] = self._help_text.index("end-1c")
        self._h("11. SQL-запросы для диагностики", "h1")
        self._p("Расширенный справочник SQL-запросов для диагностики opencode-баз через MCP-инструмент oc_query или прямой SQLite.")

        self._h("11.1 Базовые запросы", "h2")
        self._p("Подсчёт сессий в каждой БД:")
        self._code("  SELECT 'opencode.db' AS db, COUNT(*) AS sessions FROM session")
        self._code("  UNION ALL")
        self._code("  SELECT 'opencode-dev.db', COUNT(*) FROM main.session;")
        self._p("Корневые сессии (без parent_id):")
        self._code("  SELECT COUNT(*) AS root_sessions FROM session WHERE parent_id IS NULL;")
        self._p("Subagent-сессии (с parent_id):")
        self._code("  SELECT COUNT(*) AS child_sessions FROM session WHERE parent_id IS NOT NULL;")
        self._p("Сессии по проектам:")
        self._code("  SELECT p.worktree, COUNT(s.id) AS sessions FROM session s")
        self._code("  JOIN project p ON s.project_id = p.id GROUP BY p.id ORDER BY sessions DESC;")
        self._p("Сессии с project_id='global':")
        self._code("  SELECT id, title, time_created, time_archived FROM session")
        self._code("  WHERE project_id = 'global' AND parent_id IS NULL ORDER BY time_created DESC;")
        self._p("Самые тяжёлые сессии:")
        self._code("  SELECT s.id, s.title, ROUND(SUM(LENGTH(p.data)) / 1048576.0, 1) AS size_mb,")
        self._code("         COUNT(DISTINCT m.id) AS messages FROM session s")
        self._code("  JOIN part p ON p.session_id = s.id JOIN message m ON m.session_id = s.id")
        self._code("  GROUP BY s.id ORDER BY size_mb DESC LIMIT 20;")

        self._h("11.2 Архивные запросы", "h2")
        self._p("Найти все архивные сессии:")
        self._code("  SELECT id, title, time_archived FROM session")
        self._code("  WHERE time_archived IS NOT NULL ORDER BY time_archived DESC;")
        self._p("Заархивировать сессии старше 30 дней:")
        self._code("  UPDATE session SET time_archived = unixepoch() * 1000")
        self._code("  WHERE time_archived IS NULL")
        self._code("    AND time_created < (unixepoch() - 30*86400) * 1000;")
        self._p("Заархивировать одну сессию:")
        self._code("  UPDATE session SET time_archived = unixepoch() * 1000")
        self._code("  WHERE id = 'ses_xxxxxxxxxxxxxxxxxxxxxxxxxx';")
        self._p("Разархивировать все:")
        self._code("  UPDATE session SET time_archived = NULL WHERE time_archived IS NOT NULL;")

        self._h("11.3 Orphan-запросы", "h2")
        self._p("Найти orphan-детей (родитель удалён):")
        self._code("  SELECT s1.id AS child_id, s1.parent_id, s1.title FROM session s1")
        self._code("  WHERE s1.parent_id IS NOT NULL")
        self._code("    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id);")
        self._p("Найти orphan-проекты (сессии без существующего project_id):")
        self._code("  SELECT s.id, s.title, s.project_id FROM session s")
        self._code("  WHERE s.project_id != 'global'")
        self._code("    AND NOT EXISTS (SELECT 1 FROM project p WHERE p.id = s.project_id);")
        self._p("Дубли project_id (один worktree — разные хеши):")
        self._code("  SELECT worktree, COUNT(*) AS cnt, GROUP_CONCAT(id, ', ') AS project_ids")
        self._code("  FROM project WHERE id != 'global' GROUP BY worktree HAVING COUNT(*) > 1;")
        self._p("Удалить orphan-детей (каскадно):")
        self._code("  DELETE FROM part WHERE session_id IN (")
        self._code("    SELECT s1.id FROM session s1 WHERE s1.parent_id IS NOT NULL")
        self._code("    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)")
        self._code("  );")
        self._code("  DELETE FROM message WHERE session_id IN (")
        self._code("    SELECT s1.id FROM session s1 WHERE s1.parent_id IS NOT NULL")
        self._code("    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = s1.parent_id)")
        self._code("  );")
        self._code("  DELETE FROM session WHERE parent_id IS NOT NULL")
        self._code("    AND NOT EXISTS (SELECT 1 FROM session s2 WHERE s2.id = session.parent_id);")
        self._tip("После массовых удалений выполните VACUUM для возврата места на диске.")

        self._h("11.4 Запросы по проектам", "h2")
        self._p("Все известные проекты:")
        self._code("  SELECT id, worktree, vcs, time_created FROM project ORDER BY time_created DESC;")
        self._p("Сессии на проект (включая проекты без сессий):")
        self._code("  SELECT p.worktree, COUNT(s.id) AS sessions FROM session s")
        self._code("  RIGHT JOIN project p ON s.project_id = p.id")
        self._code("  GROUP BY p.id ORDER BY sessions DESC;")
        self._p("Сбросить project_id='global' для сессий без проекта:")
        self._code("  UPDATE session SET project_id = 'global'")
        self._code("  WHERE project_id NOT IN (SELECT id FROM project WHERE id != 'global');")

        self._h("11.5 Диагностика путей", "h2")
        self._p("Найти сессии с / вместо \\ (Windows — баг Tkinter):")
        self._code("  SELECT id, title, directory FROM session WHERE directory LIKE '%/%';")
        self._p("Найти сессии с NULL или пустым path:")
        self._code("  SELECT id, title, directory FROM session WHERE path IS NULL OR path = '';")

        self._h("11.6 Статистика сообщений и частей", "h2")
        self._p("Общий размер БД по таблицам:")
        self._code("  SELECT 'session' AS tbl, COUNT(*) AS rows,")
        self._code("    ROUND(SUM(LENGTH(id) + LENGTH(title) + LENGTH(directory))")
        self._code("      / 1048576.0, 1) AS size_mb FROM session")
        self._code("  UNION ALL SELECT 'message', COUNT(*),")
        self._code("    ROUND(SUM(LENGTH(data)) / 1048576.0, 1) FROM message")
        self._code("  UNION ALL SELECT 'part', COUNT(*),")
        self._code("    ROUND(SUM(LENGTH(data)) / 1048576.0, 1) FROM part;")
        self._p("Части по типам:")
        self._code("  SELECT json_extract(data, '$.type') AS ptype, COUNT(*) AS cnt,")
        self._code("    ROUND(SUM(LENGTH(data)) / 1048576.0, 1) AS size_mb")
        self._code("  FROM part GROUP BY ptype ORDER BY size_mb DESC;")
        self._p("Процент reasoning от общего размера:")
        self._code("  SELECT ROUND(SUM(CASE WHEN json_extract(data, '$.type') = 'reasoning'")
        self._code("    THEN LENGTH(data) ELSE 0 END) * 100.0")
        self._code("    / NULLIF(SUM(LENGTH(data)), 0), 1) AS reasoning_pct FROM part;")
        self._p("Сессии за последние 30 дней:")
        self._code("  SELECT COUNT(*) AS recent_sessions FROM session")
        self._code("  WHERE parent_id IS NULL")
        self._code("    AND time_created > (strftime('%s', 'now') - 30*86400) * 1000;")

        self._h("11.7 Сравнение БД (session_diff)", "h2")
        self._p("Найти сессии в opencode.db, которых нет в opencode-dev.db:")
        self._code("  -- Используй MCP: oc_check_orphans")
        self._p("Проверить разный project_id для одной сессии в двух БД:")
        self._code("  SELECT id, project_id FROM session")
        self._code("  WHERE id = 'ses_17e2b66d4ffeGa1wwrFEn0aKNN';")

        self._h("11.8 Запросы производительности", "h2")
        self._p("Топ-20 самых тяжёлых сессий:")
        self._code("  SELECT s.id, s.title, ROUND(SUM(LENGTH(p.data)) / 1048576.0, 1) AS size_mb")
        self._code("  FROM session s JOIN part p ON p.session_id = s.id")
        self._code("  GROUP BY s.id ORDER BY size_mb DESC LIMIT 20;")
        self._p("Количество сессий по дням:")
        self._code("  SELECT DATE(time_created / 1000, 'unixepoch') AS day, COUNT(*) AS sessions")
        self._code("  FROM session WHERE parent_id IS NULL GROUP BY day ORDER BY day DESC;")
        self._p("Vacuum (освободить место после удалений):")
        self._code("  VACUUM;")
        self._tip("VACUUM может временно удвоить размер БД. Перед выполнением проверьте наличие свободного места.")
        self._sep()

        # ═══════════════════════════════════════════════════
        # 12. ДИАГНОСТИКА И РЕШЕНИЕ ПРОБЛЕМ
        # ═══════════════════════════════════════════════════
        self._help_section_pos["trouble_detailed"] = self._help_text.index("end-1c")
        self._h("12. Диагностика и решение проблем", "h1")
        self._p("Описание всех известных проблем, симптомов, диагностики и решений для opencode и Session Manager.")

        self._h("12.1 Сессия не отображается в opencode", "h2")
        self._h("Не та база данных", "h3")
        self._b("Симптом: в менеджере 60 сессий, а в opencode — 3.")
        self._code("opencode db path  → C:\\Users\\...\\opencode-dev.db")
        self._p("Dev-версия использует свою БД. Переключите БД в менеджере через селектор в тулбаре. Если opencode использует dev-БД — скопируйте сессии миграцией.")
        self._h("Сессия архивирована", "h3")
        self._b("Симптом: сессия есть в менеджере, но не видна в opencode.")
        self._code("SELECT id, title, time_archived FROM session WHERE id = '<session_id>';")
        self._p("Если time_archived IS NOT NULL — разархивируйте через менеджер.")
        self._h("Некорректный project_id", "h3")
        self._b("Симптом: сессия создана в проекте, но не отображается при его открытии.")
        self._code("SELECT s.id, s.project_id, p.worktree FROM session s")
        self._code("LEFT JOIN project p ON s.project_id = p.id WHERE s.id = '<session_id>';")
        self._h("Слэши в directory (Windows)", "h3")
        self._b("Симптом: сессия есть в БД, но не отображается в Desktop.")
        self._code("SELECT id, directory FROM session WHERE directory LIKE '%/%';")
        self._p("Исправьте через кнопку «Перенести в проект» — он нормализует пути.")

        self._h("12.2 OpenCode не запускается", "h2")
        self._h("Ошибка Tauri Store (BOM)", "h3")
        self._b("Симптом: Desktop не открывается, в renderer.log ошибка SyntaxError: Unexpected token.")
        self._p("UTF-8 BOM в global.dat. Исправить:")
        self._code("python -c \"import json; open(r'%APPDATA%\\ai.opencode.desktop\\opencode.global.dat','r',encoding='utf-8-sig') as f: d=json.load(f)")
        self._code("open(r'%APPDATA%\\ai.opencode.desktop\\opencode.global.dat','w',encoding='utf-8').write(json.dumps(d,ensure_ascii=False))\"")
        self._h("БД заблокирована (SQLITE_BUSY)", "h3")
        self._b("Симптом: OpenCode пишет «database is locked».")
        self._p("Закройте все процессы OpenCode. Если не помогает — удалите -wal и -shm файлы.")
        self._h("Порча БД (SQLITE_CORRUPT)", "h3")
        self._b("Симптом: «database disk image is malformed».")
        self._code("sqlite3 opencode.db \".dump\" > dump.sql")
        self._code("sqlite3 opencode_new.db < dump.sql")

        self._h("12.3 Проблемы с менеджером", "h2")
        self._h("Не запускается (ImportError)", "h3")
        self._code("cd Q:\\User_Data\\Desktop\\opencode-manager && python run.py")
        self._h("Ошибка при чтении БД (database is locked)", "h3")
        self._p("Закройте OpenCode (Desktop + CLI) перед запуском менеджера.")
        self._h("Кнопки «Архивировать»/«Удалить» неактивны", "h3")
        self._p("OpenCode запущен. Менеджер блокирует операции записи. Закройте OpenCode.")

        self._h("12.4 Проблемы с MCP", "h2")
        self._h("MCP не загружается", "h3")
        self._code("opencode debug config | grep -A5 mcp-opencode-db")
        self._bullet("Убедитесь что путь к python.exe — ПОЛНЫЙ (не «python», а «C:\\...\\python.exe»)")
        self._bullet("Установите пакет: pip install mcp")
        self._bullet("Проверьте JSONC-синтаксис конфига")
        self._h("MCP отвечает ошибкой no such column", "h3")
        self._p("Проверьте что таблица существует в целевой БД. Версии opencode могут иметь разные схемы.")

        self._h("12.5 Проблемы с проектами", "h2")
        self._h("Дубликат project_id для одного каталога", "h3")
        self._b("Решение: разные версии opencode вычисляют хеш по-разному. Удалите дубликат:")
        self._code("DELETE FROM project WHERE id IN (SELECT id FROM project WHERE worktree = 'Q:\\...\\opencode-manager' ORDER BY time_created DESC LIMIT -1 OFFSET 1);")
        self._h("Сессии с project_id='global' не показываются", "h3")
        self._p("project_id='global' — legacy-значение. Используйте кнопку «Перенести в проект».")

        self._h("12.6 Производительность", "h2")
        self._h("OpenCode тормозит при загрузке сессии", "h3")
        self._p("Удалите reasoning через вкладку «Сообщения» → «Удалить reasoning».")
        self._h("БД выросла до 500+ МБ", "h3")
        self._bullet("Strip Reasoning (все) — экономия ~77%")
        self._bullet("Удалить subagent — убрать вложенные сессии")
        self._bullet("Очистить осиротевшие diff'ы")
        self._bullet("Vacuum")
        self._h("Vacuum требует слишком много места", "h3")
        self._p("Если на диске < 2× размера БД — не делайте Vacuum. Вместо этого используйте PRAGMA auto_vacuum = 1; VACUUM;")

        self._h("12.7 Desktop-приложение", "h2")
        self._h("Desktop не видит новые сессии", "h3")
        self._p("Перезапустите Desktop (полностью закройте, не сворачивайте в трей).")
        self._h("Desktop показывает старые (закэшированные) сессии", "h3")
        self._bullet("Закройте Desktop")
        self._bullet("Удалите %APPDATA%\\ai.opencode.desktop\\Cache\\ (кэш Chromium)")
        self._bullet("Запустите Desktop заново")

        self._h("12.8 Дополнительные сценарии", "h2")
        self._h("Расхождение количества сессий (менеджер vs opencode)", "h3")
        self._code("SELECT parent_id, COUNT(*) as cnt FROM session GROUP BY parent_id ORDER BY cnt DESC;")
        self._p("Проверьте архивированные, orphaned и subagent-сессии.")
        self._h("Отсутствует таблица project", "h3")
        self._p("Таблица project появилась в opencode 0.27.x. Если её нет — создайте вручную:")
        self._code("CREATE TABLE IF NOT EXISTS project (id TEXT PRIMARY KEY, worktree TEXT, time_created TEXT);")
        self._h("Очистка осиротевших сессий (orphans)", "h3")
        self._code("SELECT s1.id, s1.parent_id FROM session s1")
        self._code("LEFT JOIN session s2 ON s1.parent_id = s2.id")
        self._code("WHERE s1.parent_id IS NOT NULL AND s2.id IS NULL;")
        self._h("Copy DB путает авто-детекцию", "h3")
        self._p("После копирования переключите селектор БД вручную и нажмите «Обновить».")
        self._sep()

        # ═══════════════════════════════════════════════
        # 13. GUIDES
        # ═══════════════════════════════════════════════
        self._help_section_pos["guides"] = self._help_text.index("end-1c")
        self._h("13. Пошаговые гайды", "h1")

        self._h("13.1 OpenCode тормозит — быстрая очистка БД", "h2")
        self._code("  1. Закройте OpenCode")
        self._code("  2. Откройте Session Manager")
        self._code("  3. Вкладка «Дашборд» → оцените размер")
        self._code("  4. Вкладка «Очистка» → «Strip Reasoning (все)»")
        self._code("  5. Дождитесь завершения (журнал покажет количество)")
        self._code("  6. «Vacuum БД» → дождитесь окончания")
        self._code("  7. Запустите OpenCode")
        self._tip("  Результат: БД ↓~77%, OpenCode отвечает быстрее.")
        self._p("")

        self._h("13.2 Полная очистка перед переустановкой", "h2")
        self._code("  1. Закройте OpenCode")
        self._code("  2. Откройте Session Manager")
        self._code("  3. Экспортируйте нужные сессии")
        self._code("  4. «Strip Reasoning (все)» — сократить")
        self._code("  5. «Удалить subagent» — убрать вложенные")
        self._code("  6. «Очистить снапшоты» — убрать гиты")
        self._code("  7. «Очистить осиротевшие diff'ы» — мусор")
        self._code("  8. «Vacuum БД» — финальная оптимизация")
        self._code("  9. «Оставить N последних» (если нужно)")
        self._tip("  Результат: БД минимального размера, никакого мусора.")
        self._p("")

        self._h("13.3 Сохранить сессию перед удалением", "h2")
        self._code("  1. Выберите сессию")
        self._code("  2. «Экспорт выбранных» → выберите папку")
        self._code("  3. Дождитесь сообщения об успехе")
        self._code("  4. Теперь можно удалять без страха")
        self._code("  5. Восстановление: «Импорт JSON» → выберите файл")
        self._p("")

        self._h("13.4 Оставить только 5 последних", "h2")
        self._code("  1. Вкладка «Очистка»")
        self._code("  2. В поле «Оставить:» введите 5")
        self._code("  3. «Выполнить» → подтвердите")
        self._warn("  Сначала экспортируйте важное — необратимо!")
        self._p("")

        self._h("13.5 Перенести сессию в другой проект", "h2")
        self._code("  1. Выберите ОДНУ корневую сессию")
        self._code("  2. «Перенести в проект»")
        self._code("  3. Выберите новую папку")
        self._code("  4. Подтвердите — directory обновится")
        self._tip("  Subagent-сессии переносятся автоматически.")
        self._warn("  OpenCode должен быть закрыт!")
        self._p("")

        self._h("13.6 Архивация сессий", "h2")
        self._code("  1. Выберите активные сессии")
        self._code("  2. «Архивировать» → они скроются из opencode")
        self._code("  3. Чтобы вернуть: выберите архивные → «Разархивировать»")
        self._p("")

        self._h("13.7 Диагностика через MCP", "h2")
        self._code("  Внутри opencode-сессии: попросите ассистента:")
        self._code("    «используй oc_check_orphans»")
        self._code("    «используй oc_list_sessions db=opencode-dev.db»")
        self._code("    «используй oc_query db=opencode.db sql=SELECT ...»")
        self._p("")

        self._h("13.8 Резервное копирование", "h2")
        self._b("Где лежит:")
        self._code("  %USERPROFILE%\\.local\\share\\opencode\\")
        self._b("Бэкап вручную:")
        self._code("  xcopy %USERPROFILE%\\.local\\share\\opencode D:\\backup\\ /E /I")
        self._b("Восстановление:")
        self._code("  xcopy D:\\backup\\ %USERPROFILE%\\.local\\share\\opencode\\ /E /Y")
        self._warn("  Перед бэкапом/восстановлением закройте OpenCode!")
        self._sep()

        # ═══════════════════════════════════════════════
        # 9. FAQ
        # ═══════════════════════════════════════════════
        self._help_section_pos["faq"] = self._help_text.index("end-1c")
        self._h("14. Частые вопросы (FAQ)", "h1")

        self._h("14.1 Что такое reasoning и почему он занимает 77% БД?", "h2")
        self._p("Reasoning (thinking, Chain of Thought) — внутренние рассуждения модели перед ответом.")
        self._p("Модель «думает вслух»: анализирует запрос, планирует действия, промежуточные выводы.")
        self._p("Весь этот текст сохраняется в parts с type='reasoning'.")
        self._p("Reasoning НЕ является историей диалога — его можно безопасно удалить.")
        self._tip("  Strip reasoning экономит ~77% места. Диалог не страдает.")
        self._p("")

        self._h("14.2 Что такое subagent-сессии?", "h2")
        self._p("Создаются встроенными агентами OpenCode: @explore (поиск по коду),")
        self._p("@general (общие задачи). Привязаны к родителю через parent_id.")
        self._p("Короткие, не несут ценности. Безопасно удалять через «Удалить subagent».")
        self._p("")

        self._h("14.3 Что такое session_diff?", "h2")
        self._p("JSON-файл с полным содержимым изменённых файлов в сессии.")
        self._p("Позволяет opencode восстанавливать изменения (undo/redo).")
        self._p("Может весить 40+ МБ. Удаляется при удалении сессии.")
        self._p("Осиротевшие diff'ы — файлы для удалённых сессий (очищаются отдельно).")
        self._p("")

        self._h("14.4 Почему Vacuum удваивает размер БД?", "h2")
        self._p("SQLite Vacuum: читает старый файл → пишет новый (без пустот) → удаляет старый.")
        self._p("В момент записи на диске существуют оба файла, нужно ~2× места.")
        self._warn("  При нехватке места Vacuum просто упадёт с ошибкой. БД не повредится.")
        self._p("")

        self._h("14.5 Можно ли пользоваться менеджером при запущенном OpenCode?", "h2")
        self._p("Чтение (просмотр, экспорт) — да, безопасно. SQLite поддерживает конкурентное чтение.")
        self._p("Запись (удаление, strip, vacuum) — НЕТ. Нужна эксклюзивная блокировка БД.")
        self._p("Менеджер сам блокирует кнопки записи при запущенном OpenCode.")
        self._p("")

        self._h("14.6 Как переключиться между opencode.db и opencode-dev.db?", "h2")
        self._p("В тулбаре вкладки «Сессии» → выпадающий список «БД».")
        self._p("Выберите нужную — сессии перезагрузятся из неё.")
        self._p("")

        self._h("14.7 Почему я вижу 3 сессии вместо 60?", "h2")
        self._p("Проверьте, какая БД выбрана в селекторе.")
        self._p("Если выбрана opencode-dev.db — там действительно 3-7 сессий.")
        self._p("Переключитесь на opencode.db (60 сессий).")
        self._p("")

        self._h("14.8 Как очистить осиротевшие diff'ы?", "h2")
        self._p("Меню «Инструменты» → «Очистить осиротевшие diff'ы».")
        self._p("Удаляются JSON-файлы из session_diff/ для сессий, которых уже нет в БД.")
        self._p("")

        self._h("14.9 Как работает сортировка?", "h2")
        self._p("Сессии: клик по заголовку → SQL ORDER BY → данные обновляются из БД.")
        self._p("Сообщения: клик по «Дата и время» → перезагрузка с новым ORDER BY.")
        self._p("Клик по «Тип», «Статус», «Размер» → in-memory сортировка видимого.")
        self._p("Дашборд (топ-10): in-memory сортировка.")
        self._p("")

        self._h("14.10 Что такое компактированные сообщения?", "h2")
        self._p("OpenCode автоматически сжимает старые сессии: оставляет только структуру")
        self._p("(role + summary), удаляет части. Отображаются как «(компактировано)».")
        self._p("Скрываются чекбоксом «Только с контентом».")
        self._p("")

        self._h("14.11 Как найти проблемный вызов инструмента?", "h2")
        self._code("  1. Откройте сессию (двойной клик)")
        self._code("  2. «Удалить ошибки» — убирает всё со статусом error")
        self._code("  3. Или отсортируйте по «Статусу» (▼) — error-записи сверху")
        self._code("  4. Выберите проблемные → «Удалить выбранные»")
        self._p("")

        self._h("14.12 Что такое orphan-сессии и почему они красные?", "h2")
        self._p("Orphan = дочерняя сессия (subagent), чей родитель был удалён.")
        self._p("Они не привязаны ни к чему и бесполезны — можно удалять.")
        self._p("Показываются красным в отдельной секции дерева.")
        self._p("")

        self._h("14.13 Как работает MCP-инспектор?", "h2")
        self._p("MCP-сервер подключается в opencode.jsonc. Инструменты доступны LLM-агенту.")
        self._p("Пользователь их НЕ ВИДИТ в UI — только AI может их вызывать.")
        self._p("После изменения конфига нужен перезапуск opencode.")
        self._p("")

        self._h("14.14 Как сделать бэкап всей БД?", "h2")
        self._p("Ручное копирование папки (см. Гайд 13.8).")
        self._p("Или экспорт отдельных сессий через «Экспорт всех».")
        self._p("Встроенной функции бэкапа всей БД пока нет.")
        self._p("")

        self._h("14.15 Как восстановить сессию из JSON?", "h2")
        self._p("Нажмите «Импорт JSON» → выберите .json файл.")
        self._p("Программа создаст бэкап текущей БД, затем импортирует сессию.")
        self._warn("  Импорт перезаписывает сессию с тем же ID! Создаётся авто-бэкап.")
        self._sep()
        # ── 12. Glossary ──
        self._help_section_pos["glossary"] = self._help_text.index("end-1c")
        self._h("15. Глоссарий", "h1")

        self._table_row(["Термин", "Описание"], header=True)
        self._table_row(["Сессия", "Один диалог с AI-агентом. Хранится в таблице session."])
        self._table_row(["Корневая сессия", "Сессия без parent_id. Создана пользователем."])
        self._table_row(["Subagent", "Дочерняя сессия (parent_id IS NOT NULL). Создана @explore/@general."])
        self._table_row(["Orphan", "Subagent, чей родитель удалён. Показывается красным с ⚠."])
        self._table_row(["Reasoning", "Chain of Thought. Thinking-токены. ~77% размера БД. Можно удалить."])
        self._table_row(["Part", "Фрагмент сообщения: text, tool, reasoning, patch, step-start и др."])
        self._table_row(["Message", "Цельное сообщение: запрос пользователя или ответ ассистента."])
        self._table_row(["Token", "Единица измерения текста для LLM. Примерно 4 символа = 1 токен."])
        self._table_row(["Project", "Git-репозиторий, в котором велась сессия. Идентифицируется по хешу."])
        self._table_row(["project_id", "SHA1 хеш git-корня. 'global' для сессий без проекта."])
        self._table_row(["parent_id", "ID родительской сессии. NULL для корневых."])
        self._table_row(["time_archived", "Timestamp архивации. NULL = активна. opencode скрывает архивные."])
        self._table_row(["Directory (directory)", "Путь к проекту. Влияет на отображение сессии в opencode."])
        self._table_row(["Session diff", "JSON-файл с изменениями файлов. Хранится отдельно от БД."])
        self._table_row(["WAL", "Write-Ahead Log. Журнал транзакций SQLite. Ускоряет запись."])
        self._table_row(["Vacuum", "Пересборка SQLite-файла. Сжимает, но временно ×2 размер."])
        self._table_row(["MCP", "Model Context Protocol. Протокол для расширения возможностей AI."])
        self._table_row(["Doc-ID", "Уникальный идентификатор документации. Используется для кросс-ссылок."])
        self._table_row(["BOM", "Byte Order Mark (EF BB BF). Маркер UTF-8. Tauri Store его не понимает."])
        self._p("")

        # ═══════════════════════════════════════════════════
        # 16. Migration between DBs
        # ═══════════════════════════════════════════════════
        self._help_section_pos["migration"] = self._help_text.index("end-1c")
        self._h("16. Миграция сессий между базами данных", "h1")
        self._p("На системе может быть несколько SQLite-баз OpenCode. Основная проблема: CLI и Desktop могут использовать разные БД.")
        self._p("CLI (npm, dev) → opencode-dev.db, Desktop (latest) → opencode.db.")

        self._h("16.1 Таблица баз данных", "h2")
        self._table_row(["База", "Размер", "Сессий", "Когда создана"], header=True)
        self._table_row(["opencode.db", "192 MB", "60", "Стабильная версия"])
        self._table_row(["opencode-dev.db", "0.8 MB", "7", "Dev-сборка из npm"])
        self._table_row(["opencode1.db", "192 MB", "51", "Ручная копия"])
        self._table_row(["opencode - копия.db", "192 MB", "51", "Системная копия"])

        self._h("16.2 Миграция одной сессии через менеджер", "h2")
        self._code("  1. Откройте менеджер")
        self._code("  2. Выберите исходную БД в тулбаре (селектор)")
        self._code("  3. Найдите нужную сессию")
        self._code("  4. Нажмите «Экспорт выбранных» → сохраните JSON")
        self._code("  5. Переключитесь на целевую БД")
        self._code("  6. Нажмите «Импорт JSON» → выберите сохранённый файл")
        self._tip("При импорте создаётся автоматический бэкап целевой БД.")

        self._h("16.3 Миграция всех сессий", "h2")
        self._code("  1. Выберите исходную БД (opencode.db)")
        self._code("  2. «Экспорт всех» → сохраните все JSON в одну папку")
        self._code("  3. Переключитесь на целевую БД (opencode-dev.db)")
        self._code("  4. «Импорт JSON» → выберите все файлы (Ctrl+A)")

        self._h("16.4 Прямое копирование через SQL", "h2")
        self._p("Для копирования одной сессии между БД можно использовать ATTACH:")
        self._code("  ATTACH DATABASE 'path/to/opencode-dev.db' AS dev;")
        self._code("  INSERT INTO dev.session SELECT * FROM main.session")
        self._code("  WHERE id = 'ses_xxxxxxxxxxxxxxxxxxxxxxxxxx';")
        self._code("  INSERT INTO dev.message SELECT * FROM main.message")
        self._code("  WHERE session_id = 'ses_xxxxxxxxxxxxxxxxxxxxxxxxxx';")
        self._code("  INSERT INTO dev.part SELECT * FROM main.part")
        self._code("  WHERE session_id = 'ses_xxxxxxxxxxxxxxxxxxxxxxxxxx';")
        self._code("  DETACH DATABASE dev;")
        self._warn("Прямой SQL требует отключения OpenCode и осторожности.")

        self._h("16.5 Исправление project_id после миграции", "h2")
        self._p("После копирования сессии в новую БД может не совпадать project_id:")
        self._code("  UPDATE session SET project_id = 'a8a2d42272aeac95b2502345313a1f1866da532a'")
        self._code("  WHERE directory LIKE '%TestQA%' AND project_id = 'global';")

        self._h("16.6 После миграции", "h2")
        self._code("  1. Перезапустите opencode (Desktop или CLI)")
        self._code("  2. Проверьте что сессии отображаются")
        self._code("  3. Удалите старые сессии из исходной БД (если нужно)")

        self._h("16.7 Известные проблемы", "h2")
        self._bullet("Разные project_id для одного каталога — исправляется UPDATE")
        self._bullet("Archived сессии — после миграции нужно разархивировать")
        self._bullet("Session_diff не копируется при экспорте/импорте JSON (только БД)")
        self._bullet("Если родитель не скопирован, дети станут orphan'ами")
        self._sep()

        # ═══════════════════════════════════════════════════
        # 17. Architecture
        # ═══════════════════════════════════════════════════
        self._help_section_pos["architecture"] = self._help_text.index("end-1c")
        self._h("17. Архитектура OpenCode (две копии)", "h1")
        self._p("На системе пользователя установлены ДВЕ копии OpenCode, которые используют разные базы данных. Понимание этой архитектуры критически важно для диагностики.")

        self._h("17.1 OpenCode CLI / TUI (dev)", "h2")
        self._table_row(["Свойство", "Значение"], header=True)
        self._table_row(["Путь", "C:\\Users\\...\\npm\\node_modules\\opencode"])
        self._table_row(["Канал", "dev"])
        self._table_row(["Версия", "Сборка из репозитория"])
        self._table_row(["База данных", "opencode-dev.db"])
        self._table_row(["Сессий", "~5-7"])
        self._table_row(["Запуск", "Из терминала: opencode"])
        self._table_row(["Интерфейс", "CLI / TUI (терминал)"])

        self._h("17.2 OpenCode Desktop", "h2")
        self._table_row(["Свойство", "Значение"], header=True)
        self._table_row(["Путь", "C:\\Users\\...\\@opencode-ai\\desktop\\OpenCode.exe"])
        self._table_row(["Канал", "latest"])
        self._table_row(["Версия", "1.15.13 (packaged, Tauri)"])
        self._table_row(["База данных", "opencode.db"])
        self._table_row(["Сессий", "~56-60"])
        self._table_row(["Запуск", "Через ярлык / меню Пуск"])
        self._table_row(["Интерфейс", "GUI (Tauri + WebView)"])

        self._h("17.3 Как OpenCode выбирает базу данных", "h2")
        self._p("Файл: packages/core/src/database/database.ts")
        self._code("  function path():")
        self._code("    # 1. OPENCODE_DB env var — абсолютный override")
        self._code("    if (Flag.OPENCODE_DB) return resolve(Flag.OPENCODE_DB)")
        self._code("    # 2. Каналы latest/beta/prod → opencode.db")
        self._code("    if ([\"latest\",\"beta\",\"prod\"].includes(channel)")
        self._code("        || env.OPENCODE_DISABLE_CHANNEL_DB)")
        self._code("      return join(data, \"opencode.db\")")
        self._code("    # 3. Всё остальное → opencode-{channel}.db")
        self._code("    return join(data, \"opencode-${channel}.db\")")
        self._tip("Desktop и CLI могут использовать РАЗНЫЕ базы данных. Всегда проверяйте opencode db path.")

        self._h("17.4 Desktop-приложение (Tauri)", "h2")
        self._p("Desktop-приложение (OpenCode.exe) версии 1.15+ — это Tauri app (Rust + web).")
        self._p("Оно НЕ ИСПОЛЬЗУЕТ opencode.db напрямую — а запускает sidecar (opencode из npm) и общается с ним через HTTP API (http://127.0.0.1:56974).")
        self._p("Sidecar — тот же opencode CLI, который использует opencode-dev.db.")
        self._p("Состояние UI хранится в .dat файлах Tauri Store:")
        self._code("  %APPDATA%\\ai.opencode.desktop\\opencode.global.dat")
        self._code("  %APPDATA%\\ai.opencode.desktop\\opencode.workspace.*.dat")

        self._h("17.5 Логи Desktop-приложения", "h2")
        self._code("  %APPDATA%/ai.opencode.desktop/logs/{timestamp}/")
        self._code("  ├── main.log       — main process (startup, sidecar, lifecycle)")
        self._code("  ├── server.log     — sidecar server process")
        self._code("  ├── renderer.log   — WebView renderer (UI errors, store errors)")
        self._code("  ├── crash.log      — crash reporter")
        self._code("  ├── network.log    — HTTP communication")
        self._code("  └── pty.log        — terminal emulator")

        self._h("17.6 Проектная система (project_id)", "h2")
        self._p("project_id — SHA1 хеш git-корня рабочей директории:")
        self._code("  1. opencode запускается в Q:\\...\\TestQA")
        self._code("  2. git rev-parse --show-toplevel → Q:/.../TestQA")
        self._code("  3. lowercased → SHA1 → a8a2d42272aeac95b2502345313a1f1866da532a")
        self._code("  4. INSERT INTO project (id, worktree)")
        self._code("  5. Все новые сессии получают этот project_id")
        self._p("Старые сессии могут иметь project_id='global' — это legacy-значение.")

        self._h("17.7 Ключевые архитектурные решения", "h2")
        self._table_row(["Решение", "Обоснование"], header=True)
        self._table_row(["Channel-based DB isolation", "Dev и stable не пересекаются"])
        self._table_row(["project_id на каждой сессии", "Список сессий без JOIN"])
        self._table_row(["parent_id для subagent", "Чистое дерево; TUI фильтрует IS NULL"])
        self._table_row(["Global project — реальная запись", "Каждая сессия имеет project_id"])
        self._table_row(["No cascade delete на project", "Сессии переживают переидентификацию"])
        self._table_row(["Sidecar архитектура (Desktop)", "Единый API для Desktop и CLI"])
        self._table_row(["Tauri Store для UI state", "Отдельно от SQLite; реактивный фронтенд"])
        self._sep()

        self._help_text.config(state=tk.DISABLED)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
