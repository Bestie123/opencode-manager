"""OpenCode Session Manager - GUI приложение.
Tkinter-интерфейс для управления сессиями OpenCode.
"""

import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
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
        ttk.Button(actions, text="Перенести в проект", command=self._change_session_directory).pack(side=tk.LEFT, padx=2)

        self.selected_label = ttk.Label(actions, text="Выбрано: 0")
        self.selected_label.pack(side=tk.RIGHT, padx=5)

        # Treeview — с поддержкой выделения
        tree_frame = ttk.Frame(frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=(0, 5))

        columns = ("title", "directory", "size", "messages", "tokens_in", "tokens_out", "reasoning", "age", "model")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", selectmode="extended")

        self.tree.heading("title", text="Название", command=lambda: self._sort_sessions("title"))
        self.tree.heading("directory", text="Директория", command=lambda: self._sort_sessions("directory"))
        self.tree.heading("size", text="Размер", command=lambda: self._sort_sessions("size"))
        self.tree.heading("messages", text="Сообщ.", command=lambda: self._sort_sessions("messages"))
        self.tree.heading("tokens_in", text="Tokens In", command=lambda: self._sort_sessions("tokens_in"))
        self.tree.heading("tokens_out", text="Tokens Out", command=lambda: self._sort_sessions("tokens_out"))
        self.tree.heading("reasoning", text="Reasoning", command=lambda: self._sort_sessions("reasoning"))
        self.tree.heading("age", text="Возраст", command=lambda: self._sort_sessions("age"))
        self.tree.heading("model", text="Модель", command=lambda: self._sort_sessions("model"))

        self.tree.column("title", width=250, minwidth=120)
        self.tree.column("directory", width=200, minwidth=100)
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

    def _on_tree_double_click(self, event):
        sel = self.tree.selection()
        if sel:
            session = self._session_map.get(sel[0])
            if session:
                self._open_messages_for_session(session.id)

    def _sort_sessions(self, col):
        if self._session_sort_col == col:
            self._session_sort_asc = not self._session_sort_asc
        else:
            self._session_sort_col = col
            self._session_sort_asc = True

        # Update header arrows
        for c in ("title", "directory", "size", "messages", "tokens_in", "tokens_out", "reasoning", "age", "model"):
            arrow = ""
            if c == self._session_sort_col:
                arrow = " ▲" if self._session_sort_asc else " ▼"
            label = {"title": "Название", "directory": "Директория", "size": "Размер", "messages": "Сообщ.",
                     "tokens_in": "Tokens In", "tokens_out": "Tokens Out",
                     "reasoning": "Reasoning", "age": "Возраст", "model": "Модель"}[c]
            self.tree.heading(c, text=label + arrow)

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
            ("БД", stats.db_size_bytes),
            ("Diffs", stats.session_diff_size_bytes),
            ("Снапшоты", stats.snapshot_size_bytes),
        ]
        total = sum(c[1] for c in components) or 1
        bar_frame = ttk.Frame(chart_box)
        bar_frame.pack(fill=tk.X)
        for name, size in components:
            pct = (size / total) * 100
            row = ttk.Frame(bar_frame)
            row.pack(fill=tk.X, pady=1)
            ttk.Label(row, text=f"{name}:", width=15).pack(side=tk.LEFT)
            bw = max(1, int(pct * 3))
            ttk.Label(row, text="█" * bw + f" {pct:.1f}% ({self._fmt_size(size)})").pack(side=tk.LEFT, padx=5)

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
        self.status_var.set(f"Загружено {len(sessions)} сессий")

    def _filter_sessions(self, *args):
        search = self.search_var.get().lower()
        filtered = [s for s in self._all_sessions if search in s.title.lower() or search in s.id.lower()]
        self._sessions = filtered
        self._refresh_tree()

    def _refresh_tree(self):
        self.tree.delete(*self.tree.get_children())
        self._session_map = {}
        for s in self._sessions:
            self._insert_session(s)

    def _insert_session(self, s):
        model = s.model.split("/")[-1] if s.model else ""
        try:
            m = json.loads(s.model)
            model = m.get("id", s.model)
        except:
            pass
        # Shorten directory path for display
        directory = s.directory or ""
        if directory.startswith("Q:\\User_Data\\Desktop\\"):
            directory = directory[22:]
        elif directory.startswith("C:\\Users\\"):
            directory = "~" + directory[11:]
        iid = self.tree.insert("", tk.END, values=(
            s.title[:60], directory, s.size_str, s.message_count,
            f"{s.tokens_input:,}", f"{s.tokens_output:,}",
            f"{s.tokens_reasoning:,}", s.age_str, model
        ))
        self._session_map[iid] = s
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
            ("2. Сессии", "sessions"),
            ("3. Сообщения", "messages"),
            ("4. Очистка", "cleanup"),
            ("5. Дашборд", "dashboard"),
            ("6. Хранилище", "storage"),
            ("7. Меню", "menu"),
            ("8. Гайды", "guides"),
            ("9. FAQ", "faq"),
        ]

        self._help_nav_data = nav_items
        self._help_buttons = []
        for label, tag in nav_items:
            btn = ttk.Button(self._help_nav_buttons_frame, text=label,
                             command=lambda t=tag: self._scroll_to_tag(t))
            btn.pack(fill=tk.X, pady=1)
            self._help_buttons.append((tag, btn))

        ttk.Separator(nav_frame, orient=tk.HORIZONTAL).pack(fill=tk.X, pady=5)
        ttk.Label(nav_frame, text="Справка v1.1", font=("Segoe UI", 8)).pack(anchor=tk.W)

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

        # ── 1. Overview ──
        self._help_section_pos["overview"] = self._help_text.index("end-1c")
        self._h("1. О программе", "h1")

        self._p("OpenCode Session Manager — это GUI-инструмент для управления базами данных OpenCode.")
        self._p("OpenCode — ИИ-агент для написания кода. Каждый диалог с агентом сохраняется как «сессия»")
        self._p("в SQLite-базе данных. Со временем база разрастается: reasoning-токены, ошибки, сломанные")
        self._p("вызовы инструментов — всё это занимает место и замедляет работу OpenCode.")
        self._p("")
        self._b("Для чего нужна программа:")
        self._bullet("Просматривать содержимое сессий в удобном чат-формате")
        self._bullet("Анализировать, какие сессии занимают больше всего места")
        self._bullet("Удалять устаревшие или ненужные сессии")
        self._bullet("Очищать reasoning-токены (отнимают ~77% БД, не влияют на историю)")
        self._bullet("Экспортировать сессии в JSON для бэкапа или передачи")
        self._bullet("Импортировать сессии из JSON")
        self._bullet("Оптимизировать БД через Vacuum и очистку мусора")
        self._p("")
        self._b("Технические характеристики:")
        self._bullet("Работает напрямую с SQLite-базой opencode.db (только чтение для просмотра)")
        self._bullet("Не требует внешних зависимостей — только Python 3.10+ (tkinter в комплекте)")
        self._bullet("Поддерживает Windows, Linux, macOS")
        self._bullet("Лёгкий интерфейс на Tkinter без браузерных компонентов")
        self._p("")
        self._b("Интерфейс:")
        self._bullet("Вкладки: Сессии, Сообщения, Очистка, Дашборд, Справка")
        self._bullet("Строка меню: Файл, Инструменты, Справка")
        self._bullet("Статус-бар внизу: текущее состояние и прогресс")
        self._bullet("Тёмная тема (как OpenCode Desktop) с запоминанием выбора")
        self._sep()

        # ── 2. Sessions ──
        self._help_section_pos["sessions"] = self._help_text.index("end-1c")
        self._h("2. Вкладка «Сессии»", "h1")

        self._h("2.1 Назначение", "h2")
        self._p("Просмотр всех сессий OpenCode. Каждая строка — одна сессия (диалог с агентом).")
        self._p("Отсюда можно открыть сообщения сессии, удалить, экспортировать или очистить.")
        self._p("")

        self._h("2.2 Колонки таблицы", "h2")
        self._table_row(["Колонка", "Описание"], header=True)
        self._table_row(["Название", "Заголовок сессии (первое сообщение или автоназвание)"])
        self._table_row(["Размер", "Суммарный размер всех частей (text + tool + reasoning + ...)"])
        self._table_row(["Сообщ.", "Количество сообщений: user + assistant"])
        self._table_row(["Tokens In", "Входящие токены — текст запросов и вызовы инструментов"])
        self._table_row(["Tokens Out", "Исходящие токены — ответы модели"])
        self._table_row(["Reasoning", "Thinking-токены (Chain of Thought) — можно удалить без потерь"])
        self._table_row(["Возраст", "Относительное время: сегодня, 3д назад, 2 недели, 1 месяц"])
        self._table_row(["Модель", "ID модели: mimo-v2.5-free, gpt-4.1, claude-sonnet-4-20250514 и др."])
        self._p("")
        self._tip("  Клик по заголовку столбца — сортировка. Повторный клик — меняет направление ▲/▼.")
        self._p("")

        self._h("2.3 Выбор сессий", "h2")
        self._bullet("Одиночный клик — выбрать одну")
        self._bullet("Ctrl + Click — добавить/убрать из множественного выбора")
        self._bullet("Shift + Click — выбрать диапазон от текущей до кликнутой")
        self._bullet("Двойной клик — открыть вкладку «Сообщения» с этой сессией")
        self._p("")
        self._tip("  Внизу отображается «Выбрано: N», можно не гадать сколько выбрано.")
        self._p("")

        self._h("2.4 Поиск и сортировка", "h2")
        self._p("Поле поиска фильтрует сессии по названию или ID в реальном времени.")
        self._code("  Введите «qtest» → останутся только сессии с упоминанием qtest")
        self._code("  Введите «ses_183» → покажет сессии, ID которых начинается на ses_183")
        self._p("")
        self._p("Выпадающий список сортировки (комбо-бокс) позволяет выбрать поле для сортировки.")
        self._p("Клик по заголовку любого столбца — быстрая сортировка по этому полю.")
        self._p("")
        self._b("Пример использования сортировки:")
        self._code("  Сортировка по «Размеру» (▼) — самые тяжёлые сессии сверху")
        self._code("  Сортировка по «Возрасту» (▼) — самые старые сессии сверху")
        self._code("  Сортировка по «Reasoning» (▼) — найти сессии, где много thinking-токенов")
        self._p("")

        self._h("2.5 Кнопки панели", "h2")
        self._table_row(["Кнопка", "Что делает"], header=True)
        self._table_row(["Экспорт выбранных", "Сохраняет JSON-файлы сессий в выбранную папку"])
        self._table_row(["Экспорт всех", "Экспортирует все сессии без выбора"])
        self._table_row(["Импорт JSON", "Загружает сессии из ранее экспортированных JSON-файлов"])
        self._table_row(["Удалить выбранные", "Безвозвратно удаляет сессии из БД + diff-файлы"])
        self._table_row(["Strip reasoning", "Удаляет thinking-токены из выбранных сессий (~77% размера)"])
        self._table_row(["Перенести в проект", "Изменяет привязку сессии к другому проекту"])
        self._table_row(["Открыть сообщения", "Переход на вкладку «Сообщения» для выбранной сессии"])
        self._table_row(["Обновить", "Принудительная перезагрузка списка сессий из БД"])
        self._p("")
        self._tip("  Экспорт — единственный безопасный способ сохранить сессию перед удалением.")
        self._p("")

        self._h("2.6 Горячие клавиши", "h2")
        self._bullet("F5 — обновить список сессий")
        self._bullet("Меню «Файл» → «Обновить (F5)»")
        self._p("")

        self._h("2.7 Пример: Найти и удалить самую большую сессию", "h2")
        self._code("  1. Откройте Session Manager")
        self._code("  2. Кликните по заголовку «Размер» дважды (▼)")
        self._code("  3. Самая большая сессия — первая в списке")
        self._code("  4. Выберите её (один клик)")
        self._code("  5. Нажмите «Экспорт выбранных» → сохраните на всякий случай")
        self._code("  6. Нажмите «Удалить выбранные» → подтвердите")
        self._tip("  Результат: сессия удалена вместе с diff-файлом. Остальные не тронуты.")
        self._sep()

        # ── 3. Messages ──
        self._help_section_pos["messages"] = self._help_text.index("end-1c")
        self._h("3. Вкладка «Сообщения»", "h1")

        self._h("3.1 Назначение", "h2")
        self._p("Детальный просмотр содержимого одной сессии в формате чата.")
        self._p("Позволяет увидеть, что именно говорил пользователь и как отвечал ИИ-агент,")
        self._p("какие инструменты вызывались, какие файлы менялись, какие были ошибки.")
        self._p("")

        self._h("3.2 Как открыть сессию в сообщениях", "h2")
        self._bullet("Двойной клик по сессии на вкладке «Сессии»")
        self._bullet("Или выделить сессию и нажать «Открыть сообщения»")
        self._p("")

        self._h("3.3 Структура экрана", "h2")
        self._p("Экран разделён на две панели:")
        self._bullet("Слева — список сообщений в хронологическом порядке")
        self._bullet("Справа — детальный просмотр выбранного сообщения в чат-формате")
        self._p("")
        self._p("Сверху — сводка: из скольки сообщений состоит сессия, сколько частей")
        self._p("каждого типа и общий объём в мегабайтах.")
        self._p("")

        self._h("3.4 Колонки списка сообщений", "h2")
        self._table_row(["Колонка", "Описание"], header=True)
        self._table_row(["Дата и время", "Метка времени сообщения"])
        self._table_row(["Тип", "user (ваш запрос) или тип части ответа: text, tool, reasoning и др."])
        self._table_row(["Статус", "Для tool-вызовов: completed (OK) или error (ошибка)"])
        self._table_row(["Размер", "Объём данных в байтах/КБ/МБ"])
        self._table_row(["Текст / Инструмент", "Превью содержимого: первые 120 символов"])
        self._p("")
        self._tip("  Клик по любому заголовку столбца — сортировка. Дата — перезагрузка из БД,")
        self._p("  остальные — клиентская сортировка видимых строк.", tag="indent")
        self._p("")

        self._h("3.5 Фильтры отображения", "h2")
        self._p("Чекбокс «Только с контентом» — скрывает компактированные сообщения")
        self._p("(у которых нет частей). Полезно для старых сессий, где первые 50-100 сообщений")
        self._p("были сжаты и не содержат полезных данных.")
        self._code("  Включите фильтр → останутся только сообщения с parts")
        self._code("  Размер сессии в статус-баре обновится (стало меньше)")
        self._p("")

        self._h("3.6 Чат-формат (правая панель)", "h2")
        self._p("При клике на сообщение справа отображается диалог в читаемом виде:")
        self._p("")
        self._p("  «Вы  14.05.2026 15:30:01» (синий) — ваш запрос модели", tag="indent")
        self._p("  «Ассистент  14.05.2026 15:30:05» (зелёный) — ответ ИИ", tag="indent")
        self._p("")
        self._h("Типы контента в ответе ассистента:", "h3")
        self._bullet("Текст — основной ответ модели (белый/чёрный текст)")
        self._bullet("Инструменты — вызовы функций: имя (жирный), параметры, статус OK/ОШИБКА")
        self._bullet("Рассуждения — thinking-токены (серый курсив на тёмном фоне) — можно удалить")
        self._bullet("Файл — изменённый файл с путём (фиолетовый)")
        self._bullet("Шаг — разделитель между действиями ассистента (голубой)")
        self._tip("  Для компактированных сообщений отображается «(компактировано)».")
        self._p("")

        self._h("3.7 Ленивая загрузка", "h2")
        self._p("Большие сессии могут содержать тысячи сообщений. Чтобы не тормозить интерфейс,")
        self._p("загружается только 500 сообщений за раз. При прокрутке вниз (>85%) автоматически")
        self._p("подгружаются следующие 500.")
        self._p("")
        self._b("Индикация:")
        self._bullet("Статус-бар: «Загружено 2000 из 10026 сообщений»")
        self._bullet("Синхронизация с полосой прокрутки: «Загрузка... (1500/10026)»")
        self._p("")

        self._h("3.8 Множественный выбор и удаление", "h2")
        self._p("Можно выбрать несколько записей через Ctrl+Click и выполнить массовое удаление.")
        self._p("")
        self._table_row(["Кнопка", "Что удаляет"], header=True)
        self._table_row(["Удалить выбранные", "Отмеченные Ctrl+Click сообщения и все их части"])
        self._table_row(["Удалить ошибки", "Все части со статусом error в текущей сессии"])
        self._table_row(["Удалить reasoning", "Все thinking-токены в текущей сессии"])
        self._table_row(["Удалить старше 7 дн", "Сообщения старше 7 дней в текущей сессии"])
        self._table_row(["Обновить", "Перезагрузить список сообщений после изменений"])
        self._p("")
        self._tip("  После удаления части сессия пересчитывается: обновляются токены и размер.")
        self._p("")

        self._h("3.9 Статус-бар сообщений", "h2")
        self._p("Под панелью кнопок отображается сводка:")
        self._code("  Сообщений: 500 | Parts: 145.2 MB | text: 2500 (12.3 MB) | tool: 800 (89.1 MB) | ...")
        self._p("Это помогает быстро оценить, какие типы данных занимают больше всего места.")
        self._p("")

        self._h("3.10 Пример: Сократить размер monster-сессии", "h2")
        self._p("Проблема: 2500 сообщений, 145 МБ parts, OpenCode тормозит при загрузке.")
        self._code("  1. Откройте проблемную сессию (двойной клик)")
        self._code("  2. Нажмите «Удалить reasoning» — убирает thinking-токены")
        self._code("  3. Нажмите «Удалить ошибки» — убирает сломанные вызовы")
        self._code("  4. Рассортируйте по «Размеру» (▼) — самые тяжёлые записи сверху")
        self._code("  5. Выберите ненужные (Ctrl+Click) и нажмите «Удалить выбранные»")
        self._tip("  Результат: сессия занимает в 3-5 раз меньше места, текст диалога сохранён.")
        self._sep()

        # ── 4. Cleanup ──
        self._help_section_pos["cleanup"] = self._help_text.index("end-1c")
        self._h("4. Вкладка «Очистка»", "h1")

        self._h("4.1 Назначение", "h2")
        self._p("Пакетные операции очистки для всей базы данных или выбранных сессий.")
        self._p("Позволяет быстро сократить размер БД, не открывая каждую сессию вручную.")
        self._p("")

        self._h("4.2 Кнопки быстрых действий", "h2")
        self._table_row(["Кнопка", "Действие", "Эффект"], header=True)
        self._table_row(["Strip Reasoning (все)", "Удаляет thinking-токены из ВСЕХ сессий",
                         "БД уменьшается на ~77%, диалоги не страдают"])
        self._table_row(["Strip Reasoning (выбр.)", "Только из выбранных на вкладке «Сессии»",
                         "Точечное удаление reasoning"])
        self._table_row(["Удалить старые (>30 дн)", "Сессии старше 30 дней",
                         "Кроме subagent-сессий"])
        self._table_row(["Удалить subagent", "Вложенные сессии (@explore, @general)",
                         "Содержат parent_id, безопасно удалять"])
        self._table_row(["Очистить снапшоты", "Git-объекты в snapshot/",
                         "Файлы версий, созданные OpenCode"])
        self._table_row(["Очистить осиротевшие diff", "JSON-файлы для удалённых сессий",
                         "Мусор после ручного удаления сессий"])
        self._table_row(["Vacuum БД", "Полная пересборка SQLite-файла",
                         "Сжимает БД (временно удваивает размер файла!)"])
        self._p("")
        self._warn("  Vacuum временно удваивает размер БД — нужно ~2× свободного места.")
        self._warn("  Перед Vacuum закройте OpenCode — БД не должна быть заблокирована.")
        self._p("")

        self._h("4.3 Оставить N последних сессий", "h2")
        self._p("Поле для ввода числа. После нажатия «Выполнить» будут удалены все сессии,")
        self._p("кроме N самых свежих (по дате создания).")
        self._code("  Ввели «5» → останутся 5 последних сессий, остальные безвозвратно удалены")
        self._p("")
        self._warn("  Операция необратима! Рекомендуется сначала экспортировать нужные сессии.")
        self._p("")

        self._h("4.4 Журнал операций", "h2")
        self._p("Внизу вкладки — лог всех выполненных операций с временной меткой.")
        self._p("Пример:")
        self._code("  [14:23:01] Удаление reasoning из всех сессий...")
        self._code("  [14:23:15] Удалено 31547 reasoning-частей из 12 сессий")
        self._code("  [14:24:01] Vacuum...")
        self._code("  [14:24:35] База сжата: 142.1 MB → 32.3 MB")
        self._p("")
        self._tip("  Журнал помогает понять, какие операции были выполнены и какой эффект.")
        self._sep()

        # ── 5. Dashboard ──
        self._help_section_pos["dashboard"] = self._help_text.index("end-1c")
        self._h("5. Вкладка «Дашборд»", "h1")

        self._h("5.1 Назначение", "h2")
        self._p("Статистика хранилища OpenCode: сколько занимает база, сколько сессий,")
        self._p("сколько частей каждого типа, где находится мусор.")
        self._p("Помогает принять решение об очистке.")
        self._p("")

        self._h("5.2 Разделы дашборда", "h2")
        self._table_row(["Раздел", "Что показывает"], header=True)
        self._table_row(["База данных", "Размер opencode.db, WAL-журнала, количество сессий/сообщений/частей"])
        self._table_row(["Файловая система", "Размер session_diff (JSON-файлы изменений файлов) и snapshot/"])
        self._table_row(["Топ-10 сессий", "Самые тяжёлые сессии с сортировкой по любому столбцу"])
        self._p("")
        self._tip("  Клик по заголовку таблицы топа — сортировка. Обновить — кнопка «Обновить».")
        self._p("")

        self._h("5.3 Как читать дашборд", "h2")
        self._p("Если DB + WAL > 500 МБ → пора чистить. Reasoning занимает ~77%.")
        self._p("Если session_diff > 200 МБ → проверьте, есть ли осиротевшие файлы.")
        self._p("Если snapshot > 100 МБ → можно очистить (без потери данных).")
        self._code("  Пример: DB=142.1 MB, WAL=2.3 MB, Reasoning=109.5 MB, text=32.6 MB")
        self._code("  Вывод: strip reasoning сэкономит ~77%")
        self._sep()

        # ── 6. Storage ──
        self._help_section_pos["storage"] = self._help_text.index("end-1c")
        self._h("6. Где хранятся данные", "h1")

        self._h("6.1 Расположение файлов", "h2")
        self._p("Программа работает с теми же файлами, что и OpenCode. Ничего не копирует.")
        self._p("")

        self._h("Windows", "h2")
        self._code("  БД:       %USERPROFILE%\\.local\\share\\opencode\\opencode.db")
        self._code("  Логи:     %USERPROFILE%\\.local\\share\\opencode\\log\\")
        self._code("  Diffs:    %USERPROFILE%\\.local\\share\\opencode\\storage\\session_diff\\")
        self._code("  Снапшоты: %USERPROFILE%\\.local\\share\\opencode\\snapshot\\")
        self._code("  Конфиг:   %USERPROFILE%\\.opencode-manager\\config.json")
        self._p("")

        self._h("Linux / macOS", "h2")
        self._code("  БД:       ~/.local/share/opencode/opencode.db")
        self._code("  Логи:     ~/.local/share/opencode/log/")
        self._code("  Diffs:    ~/.local/share/opencode/storage/session_diff/")
        self._code("  Снапшоты: ~/.local/share/opencode/snapshot/")
        self._code("  Конфиг:   ~/.opencode-manager/config.json")
        self._p("")

        self._h("6.2 Что такое каждый файл", "h2")
        self._table_row(["Файл/папка", "Назначение"], header=True)
        self._table_row(["opencode.db", "SQLite-база: сессии, сообщения, части (parts)"])
        self._table_row(["opencode.db-wal", "Write-Ahead Log — журнал транзакций SQLite"])
        self._table_row(["session_diff/", "JSON-файлы с изменениями файлов в каждой сессии"])
        self._table_row(["snapshot/", "Git-подобные снимки версий файлов"])
        self._table_row(["config.json", "Настройки Session Manager: тема (dark_mode)"])
        self._p("")

        self._h("6.3 Как БД связана с интерфейсом", "h2")
        self._p("Каждая строка на вкладке «Сессии» = 1 запись в таблице session.")
        self._p("Поле directory в session — путь к проекту, в котором велась сессия.")
        self._p("OpenCode показывает сессию в том проекте, куда указывает directory.")
        self._tip("  Кнопка «Перенести в проект» меняет это поле — сессия появится в другом проекте.")
        self._p("")
        self._p("Сообщения (user/assistant) = таблица message, связана с session через session_id.")
        self._p("Фрагменты ответов (текст, вызовы инструментов, патчи) = таблица part,")
        self._p("связана с message через message_id.")
        self._p("Session_diff — внешние JSON-файлы, не входят в БД.")
        self._sep()

        # ── 7. Menu ──
        self._help_section_pos["menu"] = self._help_text.index("end-1c")
        self._h("7. Строка меню", "h1")

        self._h("7.1 Файл", "h2")
        self._table_row(["Команда", "Горячая клавиша", "Действие"], header=True)
        self._table_row(["Обновить (F5)", "F5", "Перезагрузить список сессий из БД"])
        self._table_row(["Выход", "—", "Закрыть программу"])
        self._p("")

        self._h("7.2 Инструменты", "h2")
        self._table_row(["Команда", "Действие"], header=True)
        self._table_row(["Vacuum БД", "Полная оптимизация SQLite (требует ~2× места)"])
        self._table_row(["Очистить снапшоты", "Удалить git-объекты из snapshot/"])
        self._table_row(["Очистить осиротевшие diff'ы", "Удалить JSON-файлы для несуществующих сессий"])
        self._table_row(["Удалить reasoning (все)", "Strip reasoning из всех сессий"])
        self._table_row(["Переключить тему", "Светлая/тёмная тема (запоминается в config.json)"])
        self._p("")

        self._h("7.3 Справка", "h2")
        self._table_row(["Команда", "Действие"], header=True)
        self._table_row(["О программе", "Показать окно с версией и контактами"])
        self._p("")
        self._tip("  Тема запоминается автоматически. При следующем запуске будет применена та же.")
        self._sep()

        # ── 8. Guides ──
        self._help_section_pos["guides"] = self._help_text.index("end-1c")
        self._h("8. Пошаговые гайды", "h1")

        self._h("8.1 OpenCode тормозит — быстрая очистка БД", "h2")
        self._code("  1. Закройте OpenCode")
        self._code("  2. Откройте Session Manager")
        self._code("  3. Вкладка «Дашборд» → оцените размер БД")
        self._code("  4. Вкладка «Очистка» → «Strip Reasoning (все)»")
        self._code("  5. Подождите завершения (в журнале появится количество удалённых частей)")
        self._code("  6. «Vacuum БД» → дождитесь окончания")
        self._code("  7. Запустите OpenCode")
        self._tip("  Ожидаемый результат: БД уменьшилась на ~77%, OpenCode отвечает быстрее.")
        self._p("")

        self._h("8.2 Полная очистка среды перед переустановкой", "h2")
        self._code("  1. Закройте OpenCode")
        self._code("  2. Откройте Session Manager")
        self._code("  3. Экспортируйте нужные сессии (по одной или все сразу)")
        self._code("  4. «Strip Reasoning (все)» — сократить размер")
        self._code("  5. «Удалить subagent сессии» — убрать вложенные")
        self._code("  6. «Очистить снапшоты» — убрать git-объекты")
        self._code("  7. «Очистить осиротевшие diff'ы» — убрать мусор")
        self._code("  8. «Vacuum БД» — финальная оптимизация")
        self._code("  9. «Оставить N последних» (если нужно)")
        self._tip("  Результат: БД минимального размера, нет мусора, экспортированные данные сохранены.")
        self._p("")

        self._h("8.3 Сохранить сессию перед удалением", "h2")
        self._code("  1. Выберите сессию (клик)")
        self._code("  2. «Экспорт выбранных» → выберите папку")
        self._code("  3. Дождитесь сообщения об успехе")
        self._code("  4. Теперь можно удалять без страха")
        self._tip("  Файл <id>.json можно восстановить: Откройте сессию → Импорт JSON")
        self._p("")

        self._h("8.4 Оставить только 5 последних сессий", "h2")
        self._code("  1. Вкладка «Очистка»")
        self._code("  2. В поле «Оставить:» введите 5")
        self._code("  3. «Выполнить» → подтвердите удаление")
        self._tip("  Результат: 5 свежих сессий, все остальные безвозвратно удалены.")
        self._warn("  Сначала экспортируйте важное — операция необратима!")
        self._p("")

        self._h("8.5 Найти проблемный вызов инструмента", "h2")
        self._code("  1. Откройте сессию (двойной клик)")
        self._code("  2. Нажмите «Удалить ошибки» — убирает все error-записи")
        self._code("  3. Или отсортируйте по «Статусу» (▼) — error-записи будут сверху")
        self._code("  4. Выберите проблемные и нажмите «Удалить выбранные»")
        self._p("")

        self._h("8.6 Анализ сессии в чат-формате", "h2")
        self._code("  1. Двойной клик по сессии → вкладка «Сообщения»")
        self._code("  2. Клик по любому сообщению → справа чат-формат")
        self._code("  3. Включите «Только с контентом» → скрыть компактированные")
        self._code("  4. Смотрите: синий текст = ваш запрос, зелёный = ответ ассистента")
        self._code("  5. Серый текст = рассуждения модели (можно удалить)")
        self._tip("  Полезно, когда хотите понять, что именно пошло не так в сессии.")
        self._p("")

        self._h("8.7 Резервное копирование всей БД", "h2")
        self._p("Перед любыми операциями записи (Vacuum, массовое удаление, Strip Reasoning)")
        self._p("рекомендуется сделать резервную копию базы данных. Программа сама создаёт")
        self._p("бэкап при импорте сессий, но вы можете сделать это вручную.")
        self._code("")
        self._b("Где находятся файлы для бэкапа:")
        self._code("  БД:       %USERPROFILE%\\.local\\share\\opencode\\opencode.db")
        self._code("  WAL:      %USERPROFILE%\\.local\\share\\opencode\\opencode.db-wal")
        self._code("  Diffs:    %USERPROFILE%\\.local\\share\\opencode\\storage\\session_diff\\")
        self._code("  Конфиг:   %USERPROFILE%\\.opencode-manager\\config.json")
        self._p("")
        self._b("Как сделать бэкап вручную:")
        self._code("  1. Закройте OpenCode (важно — иначе БД заблокирована)")
        self._code("  2. Скопируйте папку целиком:")
        self._code("     %USERPROFILE%\\.local\\share\\opencode\\ → D:\\backups\\opencode\\")
        self._code("  3. Или скопируйте только opencode.db + session_diff:")
        self._code("     xcopy %USERPROFILE%\\.local\\share\\opencode D:\\backups\\opencode\\ /E /I")
        self._p("")
        self._b("Как восстановить из бэкапа:")
        self._code("  1. Закройте OpenCode")
        self._code("  2. Скопируйте файлы обратно:")
        self._code("     xcopy D:\\backups\\opencode\\ %USERPROFILE%\\.local\\share\\opencode\\ /E /Y")
        self._p("")
        self._tip("  Альтернатива: экспорт отдельных сессий через кнопку «Экспорт всех».")
        self._p("  Экспортированные JSON можно импортировать обратно через «Импорт JSON».")
        self._warn("  Внимание: экспорт сессий не сохраняет session_diff — только БД!")
        self._sep()

        self._h("8.8 Перенести сессию в другой проект", "h2")
        self._p("Сессии OpenCode привязаны к проекту через поле directory в БД.")
        self._p("Кнопка «Перенести в проект» меняет этот путь — сессия появится")
        self._p("в другом проекте при открытии OpenCode.")
        self._code("")
        self._b("Как это работает:")
        self._bullet("Все сессии хранятся в одном opencode.db, независимо от проекта")
        self._bullet("Поле directory указывает, в каком проекте показывать сессию")
        self._bullet("Diff-файлы остаются на месте — они привязаны к сессии, не к проекту")
        self._bullet("Пути внутри diff'ов не меняются — они относительны корня проекта")
        self._p("")
        self._b("Как перенести:")
        self._code("  1. Выберите одну сессию на вкладке «Сессии»")
        self._code("  2. Нажмите «Перенести в проект»")
        self._code("  3. В диалоге выберите новую папку проекта")
        self._code("  4. Подтвердите — поле directory обновится")
        self._p("")
        self._tip("  После переноса сессия появится в новом проекте в OpenCode.")
        self._warn("  Diff'ы остаются со старыми путями — если структура проекта другая, откат изменений может не сработать.")
        self._p("")
        self._code("  Пример: сессия работала над qtest-runner/src/index.ts")
        self._code("  Переносим в project-alpha → diff указывает на project-alpha/src/index.ts")
        self._code("  Если такого файла нет — diff не применится, но БД не пострадает")
        self._sep()

        # ── 9. FAQ ──
        self._help_section_pos["faq"] = self._help_text.index("end-1c")
        self._h("9. Частые вопросы", "h1")

        self._h("Что такое reasoning и почему он занимает 77% БД?", "h2")
        self._p("Reasoning (он же thinking, Chain of Thought) — это «мыслительный процесс» модели.")
        self._p("Когда модель решает задачу, она внутренне проговаривает рассуждения прежде чем")
        self._p("дать ответ. Этот текст сохраняется в БД. Он занимает много места, но НЕ содержит")
        self._p("историю диалога.")
        self._tip("  Reasoning можно безопасно удалить. Текст ответов и история сохранятся.")
        self._p("")

        self._h("Что такое subagent-сессии?", "h2")
        self._p("Сессии, созданные вложенными агентами OpenCode: @explore (поиск по коду),")
        self._p("@general (общие задачи). Они привязаны к родительской сессии через parent_id.")
        self._p("Обычно короткие и не несут ценности. Безопасно удалять.")
        self._p("")

        self._h("Что такое session_diff и зачем он нужен?", "h2")
        self._p("Session_diff — это JSON-файл, в котором OpenCode хранит полное содержимое")
        self._p("изменённых файлов для каждой сессии. Позволяет восстанавливать изменения.")
        self._p("Файлы могут весить 40+ МБ каждый. Удаляются автоматически при удалении сессии.")
        self._p("Осиротевшие diff'ы — это файлы для уже удалённых сессий (их можно очистить).")
        self._p("")

        self._h("Почему Vacuum удваивает размер БД?", "h2")
        self._p("SQLite Vacuum работает так: читает старый файл целиком → пишет новый (без пустот) →")
        self._p("удаляет старый. В момент записи нового файла на диске существуют оба файла,")
        self._p("поэтому временно нужно ~2× свободного места.")
        self._warn("  БД не повредится при нехватке места — Vacuum просто упадёт с ошибкой.")
        self._p("")

        self._h("Можно ли пользоваться менеджером пока работает OpenCode?", "h2")
        self._p("Просмотр (чтение БД) — да, безопасно. SQLite поддерживает конкурентное чтение.")
        self._p("Удаление, Strip Reasoning, Vacuum — требуют эксклюзивной блокировки БД.")
        self._warn("  Закройте OpenCode перед операциями записи!")
        self._p("")

        self._h("Как работает сортировка в таблицах?", "h2")
        self._p("Вкладка «Сессии»: клик по заголовку → SQL-запрос с ORDER BY, данные обновляются.")
        self._p("Вкладка «Сообщения»: клик по «Дата и время» → перезагрузка с новым порядком.")
        self._p("Клик по «Тип», «Статус», «Размер» → in-memory сортировка видимых строк.")
        self._p("Вкладка «Дашборд» (топ-10): in-memory сортировка.")
        self._p("")

        self._h("Что такое компактированные сообщения?", "h2")
        self._p("OpenCode автоматически сжимает старые сессии: удаляет часть деталей, оставляя")
        self._p("только структуру (role + summary). Такие сообщения не имеют parts и отображаются")
        self._p("как «(компактировано)». Их можно скрыть через чекбокс «Только с контентом».")
        self._p("Текст компактированных сообщений восстановить нельзя — он не хранится в БД.")
        self._p("")

        self._h("Где хранится настройка темы?", "h2")
        self._p("В файле ~/.opencode-manager/config.json (Windows) или ~/.opencode-manager/config.json")
        self._p("(Linux/macOS). Если удалить этот файл, тема сбросится на тёмную по умолчанию.")
        self._code("  Содержимое: {\"dark_mode\": true}")
        self._p("")

        self._h("Что делать если программа не запускается?", "h2")
        self._bullet("Убедитесь, что Python 3.10+ установлен")
        self._bullet("Проверьте: python --version — должно быть 3.10 или выше")
        self._bullet("Tkinter входит в стандартную поставку Python (не нужно pip install)")
        self._bullet("Если ошибка импорта — переустановите Python с опцией «tcl/tk and IDLE»")
        self._code("  pip install opencode-manager  # НЕ НУЖНО — только стандартная библиотека")
        self._p("")

        self._help_text.config(state=tk.DISABLED)


def main():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
