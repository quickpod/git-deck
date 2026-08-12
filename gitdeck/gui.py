#!/usr/bin/env python3
r"""GitDeck -- a pure-stdlib tkinter GUI on top of the ``gitdeck`` library.

A single main window: a top bar to open a repository, a left sidebar with four
views (Changes, History, Branches, Remotes) and a main panel that swaps to the
selected view.  Every operation calls the tested core library (never
re-implements Git logic); anything that touches the network (fetch/pull/push)
runs on a background thread and is marshalled back with ``self.after``.  Failures
are shown in a clear inline result bar as the :class:`GitDeckError` message --
never a raw traceback.

Design goals baked in here (mirroring the QuickOpen house style):
  * pure standard-library tkinter/ttk -- NO third-party GUI deps.  Dark mode is
    a ttk-style + palette swap.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter is imported lazily inside main()/build_app so that merely importing
# this module (e.g. during packaging or on a headless CI box) never fails.

APP_NAME = "GitDeck"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "GitDeck — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"

DEFAULT_AUTHOR = "GitDeck User <gitdeck@localhost>"

VIEWS = [
    ("changes", "Changes"),
    ("history", "History"),
    ("branches", "Branches"),
    ("remotes", "Remotes"),
]

# ---- colour palettes (mirror the QuickOpen palette) -------------------------
PALETTES = {
    "light": {
        "bg": "#f5f7fa", "surface": "#ffffff", "text": "#141820",
        "muted": "#5b6472", "primary": "#2f5fe0", "primary_hi": "#2450c8",
        "entry": "#ffffff", "border": "#d5dae2", "sel": "#2f5fe0",
        "sel_fg": "#ffffff", "trough": "#e2e7ef", "ok": "#1f7a3d",
        "err": "#c0392b", "add": "#1f7a3d", "del": "#c0392b",
    },
    "dark": {
        "bg": "#0f1115", "surface": "#1a1e24", "text": "#f1f3f7",
        "muted": "#9aa4b2", "primary": "#5b86f7", "primary_hi": "#7098ff",
        "entry": "#1a1e24", "border": "#2a2f38", "sel": "#5b86f7",
        "sel_fg": "#0f1115", "trough": "#2a2f38", "ok": "#5bd68a",
        "err": "#ff6b5e", "add": "#5bd68a", "del": "#ff6b5e",
    },
}


# ---------------------------------------------------------------------------
# Asset / frozen handling
# ---------------------------------------------------------------------------
def asset_path(name):
    """Locate a bundled asset from source OR a PyInstaller one-file build.

    For a frozen exe we look only at ``sys._MEIPASS`` and the executable's own
    directory (never ``__file__``).  From source we also consult the package
    dir, the repo root and the CWD.  Returns an absolute path or ``None``.
    """
    roots = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            roots.append(meipass)
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
    else:
        here = os.path.dirname(os.path.abspath(__file__))
        roots += [here, os.path.dirname(here), os.getcwd()]
    for root in roots:
        candidate = os.path.join(root, name)
        if os.path.exists(candidate):
            return candidate
    return None


def open_with_default_app(path):
    """Open a file/URL with the OS default application, guarded on all platforms."""
    try:
        if hasattr(os, "startfile"):
            os.startfile(path)                # noqa: S606 - intended
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# The app (built lazily; tkinter imported only inside build_app/main)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to a live tkinter import.

    Kept inside a function so this module imports cleanly without a display.
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox

    from . import guiconfig
    from .errors import GitDeckError
    from . import (
        is_repo, init_repo, status, current_branch,
        log, commit_graph, file_diff, commit_diff,
        stage, unstage, commit,
        branches, create_branch, checkout, merge, delete_branch,
        remotes, add_remote, fetch, pull, push,
    )

    FONT = "Segoe UI"
    MONO = "Consolas"

    class App(tk.Tk):
        def __init__(self):
            super().__init__()
            self.title(WINDOW_TITLE)
            self.geometry("1120x720")
            self.minsize(920, 600)

            self.theme = guiconfig.get_theme()
            self.repo_path = None
            self._busy = False
            self._tracked = []       # (tk_widget, role) for manual re-theming
            self._img_refs = []
            self._panels = {}
            self._current_view = None

            self._set_icon()
            self._build_menu()
            self._build_layout()
            self._apply_theme()
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(50, self._open_last_or_prompt)

        # ---- assets / icon -------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("git-deck.ico")
                if ico:
                    self.iconbitmap(ico)
                    return
            except Exception:
                pass
            try:
                png = asset_path("git-deck.png")
                if png:
                    img = tk.PhotoImage(file=png)
                    self._img_refs.append(img)
                    self.iconphoto(True, img)
            except Exception:
                pass  # icon is cosmetic; never block launch

        # ---- theming -------------------------------------------------------
        def track(self, widget, role):
            self._tracked.append((widget, role))

        def _pal(self):
            return PALETTES[self.theme]

        def _apply_theme(self):
            p = self._pal()
            style = ttk.Style(self)
            try:
                style.theme_use("clam")
            except Exception:
                pass
            self.configure(bg=p["bg"])
            style.configure(".", background=p["bg"], foreground=p["text"],
                            fieldbackground=p["entry"], bordercolor=p["border"],
                            font=(FONT, 10))
            style.configure("TFrame", background=p["bg"])
            style.configure("Sidebar.TFrame", background=p["surface"])
            style.configure("Card.TFrame", background=p["surface"])
            style.configure("TLabel", background=p["bg"], foreground=p["text"])
            style.configure("Muted.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Header.TLabel", background=p["bg"], foreground=p["text"],
                            font=(FONT, 15, "bold"))
            style.configure("Sub.TLabel", background=p["bg"], foreground=p["muted"])
            style.configure("Brand.TLabel", background=p["surface"],
                            foreground=p["text"], font=(FONT, 12, "bold"))
            style.configure("Status.TLabel", background=p["surface"],
                            foreground=p["muted"])
            style.configure("TButton", background=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], focuscolor=p["surface"],
                            padding=(10, 5))
            style.map("TButton",
                      background=[("active", p["trough"]), ("disabled", p["bg"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Accent.TButton", background=p["primary"],
                            foreground="#ffffff", padding=(12, 6))
            style.map("Accent.TButton",
                      background=[("active", p["primary_hi"]),
                                  ("disabled", p["border"])],
                      foreground=[("disabled", p["muted"])])
            style.configure("Toggle.TButton", background=p["surface"],
                            foreground=p["text"], padding=(8, 4))
            style.configure("Nav.TButton", background=p["surface"],
                            foreground=p["text"], anchor="w", padding=(14, 8))
            style.map("Nav.TButton", background=[("active", p["trough"])])
            style.configure("NavActive.TButton", background=p["primary"],
                            foreground="#ffffff", anchor="w", padding=(14, 8))
            style.map("NavActive.TButton", background=[("active", p["primary_hi"])])
            for name in ("TEntry", "TSpinbox"):
                style.configure(name, fieldbackground=p["entry"], foreground=p["text"],
                                insertcolor=p["text"], bordercolor=p["border"])
            style.configure("TCheckbutton", background=p["bg"], foreground=p["text"])
            style.map("TCheckbutton", background=[("active", p["bg"])])
            style.configure("TLabelframe", background=p["bg"], foreground=p["text"],
                            bordercolor=p["border"])
            style.configure("TLabelframe.Label", background=p["bg"],
                            foreground=p["muted"])
            style.configure("Treeview", background=p["surface"],
                            fieldbackground=p["surface"], foreground=p["text"],
                            bordercolor=p["border"], rowheight=24)
            style.map("Treeview", background=[("selected", p["primary"])],
                      foreground=[("selected", p["sel_fg"])])
            style.configure("TScrollbar", background=p["surface"],
                            troughcolor=p["bg"], bordercolor=p["border"],
                            arrowcolor=p["text"])
            style.configure("TSeparator", background=p["border"])

            for widget, role in list(self._tracked):
                try:
                    if role == "listbox":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                    elif role == "text":
                        widget.configure(bg=p["surface"], fg=p["text"],
                                         insertbackground=p["text"],
                                         selectbackground=p["primary"],
                                         selectforeground=p["sel_fg"],
                                         highlightthickness=1,
                                         highlightbackground=p["border"],
                                         borderwidth=0)
                        try:
                            widget.tag_configure("add", foreground=p["add"])
                            widget.tag_configure("del", foreground=p["del"])
                            widget.tag_configure("hunk", foreground=p["primary"])
                        except Exception:
                            pass
                except Exception:
                    pass

        def toggle_theme(self):
            self.theme = "dark" if self.theme == "light" else "light"
            guiconfig.set_theme(self.theme)
            self._apply_theme()
            self._theme_btn.configure(
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")

        # ---- menu ----------------------------------------------------------
        def _build_menu(self):
            bar = tk.Menu(self)
            filem = tk.Menu(bar, tearoff=0)
            filem.add_command(label="Open Repository…", accelerator="Ctrl+O",
                              command=self._open_repo_dialog)
            filem.add_command(label="Initialise Repository…",
                              command=self._init_repo_dialog)
            self._recent_menu = tk.Menu(filem, tearoff=0)
            filem.add_cascade(label="Open Recent", menu=self._recent_menu)
            self._fill_recent_menu()
            filem.add_separator()
            filem.add_command(label="Exit", command=self.destroy)
            bar.add_cascade(label="File", menu=filem)

            viewm = tk.Menu(bar, tearoff=0)
            viewm.add_command(label="Toggle dark mode", command=self.toggle_theme)
            viewm.add_command(label="Refresh", accelerator="F5",
                              command=self.refresh)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=self._about)
            helpm.add_command(label="Open project page (quickopen.ai)",
                              command=lambda: open_with_default_app(PROJECT_URL))
            bar.add_cascade(label="Help", menu=helpm)
            self.configure(menu=bar)
            self.bind_all("<Control-o>", lambda e: self._open_repo_dialog())
            self.bind_all("<F5>", lambda e: self.refresh())

        def _fill_recent_menu(self):
            self._recent_menu.delete(0, "end")
            recent = guiconfig.get_recent()
            if not recent:
                self._recent_menu.add_command(label="(none)", state="disabled")
                return
            for path in recent:
                exists = os.path.isdir(path)
                label = path if exists else path + "   (missing)"
                self._recent_menu.add_command(
                    label=label, state="normal" if exists else "disabled",
                    command=(lambda pp=path: self.set_repo(pp)))
            self._recent_menu.add_separator()
            self._recent_menu.add_command(label="Clear list",
                                          command=self._clear_recent)

        def _clear_recent(self):
            guiconfig.clear_recent()
            self._fill_recent_menu()

        def _about(self):
            messagebox.showinfo(
                "About GitDeck",
                f"{APP_NAME} {APP_VERSION}\n\nA friendly, offline, 100% open-source "
                f"Git client built on pure-Python dulwich.\n\nPublished on "
                f"QuickOpen (quickopen.ai). Apache-2.0.")

        # ---- layout --------------------------------------------------------
        def _build_layout(self):
            top = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 8))
            top.pack(fill="x", side="top")
            ttk.Label(top, text="GitDeck", style="Brand.TLabel").pack(side="left")
            self.repo_lbl = ttk.Label(top, style="Status.TLabel",
                                      text="  no repository open")
            self.repo_lbl.pack(side="left")
            self._theme_btn = ttk.Button(
                top, style="Toggle.TButton", command=self.toggle_theme,
                text="☀ Light mode" if self.theme == "dark" else "🌙 Dark mode")
            self._theme_btn.pack(side="right")
            ttk.Button(top, text="Open Repo…", command=self._open_repo_dialog,
                       style="Toggle.TButton").pack(side="right", padx=(0, 6))

            body = ttk.Frame(self, style="TFrame")
            body.pack(fill="both", expand=True)

            side = ttk.Frame(body, style="Sidebar.TFrame", width=180)
            side.pack(side="left", fill="y")
            side.pack_propagate(False)
            self._nav_btns = {}
            for vid, label in VIEWS:
                b = ttk.Button(side, text=label, style="Nav.TButton",
                               command=(lambda v=vid: self.select_view(v)))
                b.pack(fill="x", padx=6, pady=(6, 0))
                self._nav_btns[vid] = b

            self.main = ttk.Frame(body, style="TFrame", padding=(16, 12))
            self.main.pack(side="left", fill="both", expand=True)

            self.container = ttk.Frame(self.main, style="TFrame")
            self.container.pack(fill="both", expand=True)

            # bottom result bar
            bar = ttk.Frame(self, style="Sidebar.TFrame", padding=(12, 6))
            bar.pack(fill="x", side="bottom")
            self.status_lbl = ttk.Label(bar, style="Status.TLabel", text="Ready")
            self.status_lbl.pack(side="left")
            self.result_lbl = ttk.Label(bar, style="Status.TLabel", text="")
            self.result_lbl.pack(side="left", padx=(12, 0))

        # ---- result bar helpers -------------------------------------------
        def _set_status(self, text, kind="idle"):
            p = self._pal()
            color = {"working": p["primary"], "ok": p["ok"], "err": p["err"]}.get(
                kind, p["muted"])
            self.status_lbl.configure(text=text, foreground=color)

        def _show_error(self, message):
            self.result_lbl.configure(text="✕ " + str(message),
                                      foreground=self._pal()["err"])

        def _show_ok(self, message):
            self.result_lbl.configure(text="✓ " + str(message),
                                      foreground=self._pal()["ok"])

        def _clear_result(self):
            self.result_lbl.configure(text="")

        # ---- background runner (for network ops) --------------------------
        def _bg(self, work, on_ok, busy="Working…", success="Done"):
            """Run ``work()`` off the UI thread; call ``on_ok(result)`` back on it.

            Errors are shown inline (GitDeckError message), never a traceback.
            Refuses to start a second op while one is in flight.
            """
            if self._busy:
                self._show_error("Please wait — an operation is already running.")
                return
            self._busy = True
            self._set_status(busy, kind="working")
            self._clear_result()

            def run():
                try:
                    res, err = work(), None
                except GitDeckError as ex:
                    res, err = None, str(ex)
                except Exception as ex:
                    res, err = None, f"Unexpected error: {ex}"
                self.after(0, lambda: finish(res, err))

            def finish(res, err):
                self._busy = False
                if err is not None:
                    self._set_status("Error", kind="err")
                    self._show_error(err)
                    return
                self._set_status(success, kind="ok")
                try:
                    on_ok(res)
                except Exception as ex:
                    self._show_error(f"Post-processing error: {ex}")

            threading.Thread(target=run, daemon=True).start()

        def _do(self, work, success="Done"):
            """Run a fast, local op inline (no thread), reporting errors cleanly."""
            try:
                result = work()
            except GitDeckError as ex:
                self._set_status("Error", kind="err")
                self._show_error(ex)
                return None
            except Exception as ex:
                self._set_status("Error", kind="err")
                self._show_error(f"Unexpected error: {ex}")
                return None
            self._set_status(success, kind="ok")
            return result

        # ---- repository handling ------------------------------------------
        def _open_last_or_prompt(self):
            recent = guiconfig.get_recent()
            for path in recent:
                if os.path.isdir(path) and is_repo(path):
                    self.set_repo(path)
                    return
            self.select_view("changes")

        def _open_repo_dialog(self):
            path = filedialog.askdirectory(title="Open a Git repository")
            if not path:
                return
            if not is_repo(path):
                if messagebox.askyesno(
                        "Not a repository",
                        f"{path} is not a Git repository.\n\nInitialise one here?"):
                    self._do(lambda: init_repo(path), success="Initialised")
                else:
                    return
            self.set_repo(path)

        def _init_repo_dialog(self):
            path = filedialog.askdirectory(title="Choose a folder to initialise")
            if not path:
                return
            if self._do(lambda: init_repo(path), success="Initialised") is not None:
                self.set_repo(path)

        def set_repo(self, path):
            self.repo_path = path
            guiconfig.add_recent(path)
            self._fill_recent_menu()
            branch = ""
            try:
                cb = current_branch(path)
                branch = f"  on {cb}" if cb else ""
            except Exception:
                pass
            self.repo_lbl.configure(text=f"  {path}{branch}")
            self._clear_result()
            self.select_view(self._current_view or "changes", force=True)

        def refresh(self):
            if self.repo_path:
                self.set_repo(self.repo_path)

        # ---- view switching -----------------------------------------------
        def select_view(self, vid, force=False):
            if vid == self._current_view and not force:
                return
            self._current_view = vid
            for k, b in self._nav_btns.items():
                b.configure(style="NavActive.TButton" if k == vid else "Nav.TButton")
            for child in self.container.winfo_children():
                child.destroy()
            builder = {
                "changes": self._build_changes,
                "history": self._build_history,
                "branches": self._build_branches,
                "remotes": self._build_remotes,
            }[vid]
            if not self.repo_path:
                ttk.Label(self.container, style="Sub.TLabel",
                          text="Open a repository to get started "
                               "(File → Open Repository).").pack(anchor="w")
                return
            builder(self.container)

        def _text_pane(self, parent, height=14):
            frame = ttk.Frame(parent, style="TFrame")
            txt = tk.Text(frame, height=height, wrap="none", font=(MONO, 10),
                          borderwidth=0)
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            sbx = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
            txt.configure(yscrollcommand=sb.set, xscrollcommand=sbx.set,
                          state="disabled")
            sb.pack(side="right", fill="y")
            sbx.pack(side="bottom", fill="x")
            txt.pack(side="left", fill="both", expand=True)
            self.track(txt, "text")
            return frame, txt

        def _set_diff(self, txt, text):
            txt.configure(state="normal")
            txt.delete("1.0", "end")
            if not text:
                txt.insert("end", "(no differences)")
            else:
                for line in text.splitlines(keepends=True):
                    tag = None
                    if line.startswith("+") and not line.startswith("+++"):
                        tag = "add"
                    elif line.startswith("-") and not line.startswith("---"):
                        tag = "del"
                    elif line.startswith("@@"):
                        tag = "hunk"
                    txt.insert("end", line, tag)
            txt.configure(state="disabled")

        # ---- Changes view --------------------------------------------------
        def _build_changes(self, parent):
            ttk.Label(parent, text="Changes", style="Header.TLabel").pack(anchor="w")
            ttk.Label(parent, style="Sub.TLabel",
                      text="Stage changes, review the diff, then commit.").pack(
                anchor="w", pady=(0, 8))

            body = ttk.Frame(parent, style="TFrame")
            body.pack(fill="both", expand=True)

            lists = ttk.Frame(body, style="TFrame")
            lists.pack(side="left", fill="y")

            ttk.Label(lists, text="Staged", style="Sub.TLabel").pack(anchor="w")
            self._staged_lb = tk.Listbox(lists, height=8, width=34,
                                         selectmode="extended", exportselection=False,
                                         activestyle="none")
            self._staged_lb.pack(fill="x")
            self.track(self._staged_lb, "listbox")
            ttk.Button(lists, text="Unstage selected",
                       command=self._on_unstage).pack(fill="x", pady=(4, 10))

            ttk.Label(lists, text="Unstaged / Untracked",
                      style="Sub.TLabel").pack(anchor="w")
            self._unstaged_lb = tk.Listbox(lists, height=10, width=34,
                                           selectmode="extended",
                                           exportselection=False, activestyle="none")
            self._unstaged_lb.pack(fill="x")
            self.track(self._unstaged_lb, "listbox")
            ttk.Button(lists, text="Stage selected",
                       command=self._on_stage).pack(fill="x", pady=(4, 0))
            ttk.Button(lists, text="Stage all",
                       command=self._on_stage_all).pack(fill="x", pady=(4, 0))

            right = ttk.Frame(body, style="TFrame")
            right.pack(side="left", fill="both", expand=True, padx=(12, 0))
            diff_frame, self._changes_diff = self._text_pane(right, height=12)
            diff_frame.pack(fill="both", expand=True)

            commit_box = ttk.Labelframe(right, text="Commit", padding=8)
            commit_box.pack(fill="x", pady=(10, 0))
            self._commit_msg = tk.Text(commit_box, height=3, wrap="word",
                                       font=(FONT, 10), borderwidth=0)
            self._commit_msg.pack(fill="x")
            self.track(self._commit_msg, "text")
            row = ttk.Frame(commit_box, style="TFrame")
            row.pack(fill="x", pady=(6, 0))
            ttk.Label(row, text="Author:", style="Sub.TLabel").pack(side="left")
            self._author_var = tk.StringVar(value=DEFAULT_AUTHOR)
            ttk.Entry(row, textvariable=self._author_var).pack(
                side="left", fill="x", expand=True, padx=(6, 6))
            ttk.Button(row, text="Commit", style="Accent.TButton",
                       command=self._on_commit).pack(side="right")

            for lb in (self._staged_lb, self._unstaged_lb):
                lb.bind("<<ListboxSelect>>", self._on_change_select)
            self._refresh_changes()

        def _refresh_changes(self):
            snap = self._do(lambda: status(self.repo_path), success="Refreshed")
            if snap is None:
                return
            self._staged_lb.delete(0, "end")
            for f in snap.staged:
                self._staged_lb.insert("end", f)
            self._unstaged_lb.delete(0, "end")
            for f in snap.unstaged:
                self._unstaged_lb.insert("end", f)
            for f in snap.untracked:
                self._unstaged_lb.insert("end", f + "  (new)")

        def _clean_name(self, value):
            return value[:-7] if value.endswith("  (new)") else value

        def _on_change_select(self, event):
            lb = event.widget
            sel = lb.curselection()
            if not sel:
                return
            name = self._clean_name(lb.get(sel[0]))
            staged = lb is self._staged_lb
            text = self._do(
                lambda: file_diff(self.repo_path, name, staged=staged))
            self._set_diff(self._changes_diff, text or "")

        def _on_stage(self):
            files = [self._clean_name(self._unstaged_lb.get(i))
                     for i in self._unstaged_lb.curselection()]
            if not files:
                self._show_error("Select files to stage first.")
                return
            if self._do(lambda: stage(self.repo_path, files),
                        success="Staged") is not None:
                self._refresh_changes()

        def _on_stage_all(self):
            files = [self._clean_name(self._unstaged_lb.get(i))
                     for i in range(self._unstaged_lb.size())]
            if not files:
                return
            if self._do(lambda: stage(self.repo_path, files),
                        success="Staged all") is not None:
                self._refresh_changes()

        def _on_unstage(self):
            files = [self._staged_lb.get(i)
                     for i in self._staged_lb.curselection()]
            if not files:
                self._show_error("Select staged files to unstage first.")
                return
            if self._do(lambda: unstage(self.repo_path, files),
                        success="Unstaged") is not None:
                self._refresh_changes()

        def _on_commit(self):
            message = self._commit_msg.get("1.0", "end").strip()
            author = self._author_var.get().strip() or DEFAULT_AUTHOR
            if not message:
                self._show_error("Enter a commit message first.")
                return
            sha = self._do(
                lambda: commit(self.repo_path, message, author=author),
                success="Committed")
            if sha is not None:
                self._commit_msg.delete("1.0", "end")
                self._show_ok(f"Committed {sha[:7]}")
                self._refresh_changes()

        # ---- History view --------------------------------------------------
        def _build_history(self, parent):
            ttk.Label(parent, text="History", style="Header.TLabel").pack(anchor="w")
            ttk.Label(parent, style="Sub.TLabel",
                      text="Browse commits; select one to see what it changed.").pack(
                anchor="w", pady=(0, 8))

            top = ttk.Frame(parent, style="TFrame")
            top.pack(fill="both", expand=True)

            cols = ("graph", "sha", "author", "date", "summary")
            tree = ttk.Treeview(top, columns=cols, show="headings", height=12,
                                selectmode="browse")
            for c, w in (("graph", 60), ("sha", 80), ("author", 180),
                         ("date", 170), ("summary", 380)):
                tree.heading(c, text=c.title())
                tree.column(c, width=w, anchor="w")
            sb = ttk.Scrollbar(top, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            tree.pack(side="left", fill="both", expand=True)
            self._history_tree = tree

            diff_frame, self._history_diff = self._text_pane(parent, height=12)
            diff_frame.pack(fill="both", expand=True, pady=(10, 0))

            graph = self._do(lambda: commit_graph(self.repo_path, max=200))
            if graph is None:
                return
            self._history_sha = {}
            for node in graph["nodes"]:
                rail = ("│ " * node["lane"]) + "●"
                iid = tree.insert("", "end", values=(
                    rail, node["sha"][:7], node["author"],
                    node["date"], node["summary"]))
                self._history_sha[iid] = node["sha"]
            tree.bind("<<TreeviewSelect>>", self._on_history_select)
            if not graph["nodes"]:
                self._set_diff(self._history_diff, "")

        def _on_history_select(self, event):
            sel = self._history_tree.selection()
            if not sel:
                return
            sha = self._history_sha.get(sel[0])
            text = self._do(lambda: commit_diff(self.repo_path, sha))
            self._set_diff(self._history_diff, text or "")

        # ---- Branches view -------------------------------------------------
        def _build_branches(self, parent):
            ttk.Label(parent, text="Branches", style="Header.TLabel").pack(anchor="w")
            ttk.Label(parent, style="Sub.TLabel",
                      text="Create, switch, merge and delete local branches.").pack(
                anchor="w", pady=(0, 8))

            body = ttk.Frame(parent, style="TFrame")
            body.pack(fill="both", expand=True)

            self._branch_lb = tk.Listbox(body, height=14, width=36,
                                         exportselection=False, activestyle="none")
            self._branch_lb.pack(side="left", fill="y")
            self.track(self._branch_lb, "listbox")

            btns = ttk.Frame(body, style="TFrame")
            btns.pack(side="left", fill="y", padx=(12, 0))

            new_row = ttk.Frame(btns, style="TFrame")
            new_row.pack(fill="x")
            self._new_branch_var = tk.StringVar()
            ttk.Entry(new_row, textvariable=self._new_branch_var, width=20).pack(
                side="left", padx=(0, 6))
            ttk.Button(new_row, text="Create", style="Accent.TButton",
                       command=self._on_create_branch).pack(side="left")

            ttk.Button(btns, text="Checkout selected",
                       command=self._on_checkout).pack(fill="x", pady=(12, 0))
            ttk.Button(btns, text="Merge selected → current",
                       command=self._on_merge).pack(fill="x", pady=(6, 0))
            ttk.Button(btns, text="Delete selected",
                       command=self._on_delete_branch).pack(fill="x", pady=(6, 0))

            self._refresh_branches()

        def _refresh_branches(self):
            names = self._do(lambda: branches(self.repo_path), success="Refreshed")
            if names is None:
                return
            current = current_branch(self.repo_path)
            self._branch_lb.delete(0, "end")
            self._branch_names = []
            for n in names:
                marker = "● " if n == current else "   "
                self._branch_lb.insert("end", marker + n)
                self._branch_names.append(n)

        def _selected_branch(self):
            sel = self._branch_lb.curselection()
            if not sel:
                return None
            return self._branch_names[sel[0]]

        def _on_create_branch(self):
            name = self._new_branch_var.get().strip()
            if not name:
                self._show_error("Enter a branch name first.")
                return
            if self._do(lambda: create_branch(self.repo_path, name),
                        success="Branch created") is not None:
                self._new_branch_var.set("")
                self._refresh_branches()

        def _on_checkout(self):
            name = self._selected_branch()
            if not name:
                self._show_error("Select a branch first.")
                return
            if self._do(lambda: checkout(self.repo_path, name),
                        success=f"On {name}") is not None:
                self.set_repo(self.repo_path)

        def _on_delete_branch(self):
            name = self._selected_branch()
            if not name:
                self._show_error("Select a branch first.")
                return
            if not messagebox.askyesno("Delete branch",
                                       f"Delete branch {name!r}?"):
                return
            if self._do(lambda: delete_branch(self.repo_path, name),
                        success="Branch deleted") is not None:
                self._refresh_branches()

        def _on_merge(self):
            name = self._selected_branch()
            if not name:
                self._show_error("Select a branch to merge first.")
                return
            author = getattr(self, "_author_var", None)
            author = author.get().strip() if author else DEFAULT_AUTHOR
            result = self._do(
                lambda: merge(self.repo_path, name, author=author or DEFAULT_AUTHOR))
            if result is None:
                return
            if result.status == "conflict":
                self._set_status("Merge conflict", kind="err")
                self._show_error(
                    "Merge conflict in: " + ", ".join(result.conflicts) +
                    " — resolve, stage and commit to finish.")
            elif result.status == "up_to_date":
                self._show_ok("Already up to date.")
            else:
                self._show_ok(f"Merged {name} ({result.sha[:7]}).")
            self._refresh_branches()

        # ---- Remotes view --------------------------------------------------
        def _build_remotes(self, parent):
            ttk.Label(parent, text="Remotes", style="Header.TLabel").pack(anchor="w")
            ttk.Label(parent, style="Sub.TLabel",
                      text="Manage remotes and sync. Network operations run in the "
                           "background.").pack(anchor="w", pady=(0, 8))

            cols = ("name", "url")
            tree = ttk.Treeview(parent, columns=cols, show="headings", height=8,
                                selectmode="browse")
            tree.heading("name", text="Name")
            tree.heading("url", text="URL")
            tree.column("name", width=140)
            tree.column("url", width=520)
            tree.pack(fill="x")
            self._remote_tree = tree

            add_row = ttk.Frame(parent, style="TFrame")
            add_row.pack(fill="x", pady=(10, 0))
            self._rname_var = tk.StringVar(value="origin")
            self._rurl_var = tk.StringVar()
            ttk.Label(add_row, text="Name:", style="Sub.TLabel").pack(side="left")
            ttk.Entry(add_row, textvariable=self._rname_var, width=12).pack(
                side="left", padx=(4, 8))
            ttk.Label(add_row, text="URL:", style="Sub.TLabel").pack(side="left")
            ttk.Entry(add_row, textvariable=self._rurl_var).pack(
                side="left", fill="x", expand=True, padx=(4, 8))
            ttk.Button(add_row, text="Add remote",
                       command=self._on_add_remote).pack(side="left")

            ops = ttk.Frame(parent, style="TFrame")
            ops.pack(fill="x", pady=(10, 0))
            ttk.Button(ops, text="Fetch", command=self._on_fetch).pack(side="left")
            ttk.Button(ops, text="Pull", command=self._on_pull).pack(
                side="left", padx=(6, 0))
            ttk.Button(ops, text="Push", style="Accent.TButton",
                       command=self._on_push).pack(side="left", padx=(6, 0))

            self._refresh_remotes()

        def _refresh_remotes(self):
            rems = self._do(lambda: remotes(self.repo_path), success="Refreshed")
            if rems is None:
                return
            for iid in self._remote_tree.get_children():
                self._remote_tree.delete(iid)
            for r in rems:
                self._remote_tree.insert("", "end", values=(r["name"], r["url"]))

        def _selected_remote(self):
            sel = self._remote_tree.selection()
            if sel:
                return self._remote_tree.item(sel[0], "values")[0]
            return self._rname_var.get().strip() or "origin"

        def _on_add_remote(self):
            name = self._rname_var.get().strip()
            url = self._rurl_var.get().strip()
            if self._do(lambda: add_remote(self.repo_path, name, url),
                        success="Remote added") is not None:
                self._rurl_var.set("")
                self._refresh_remotes()

        def _on_fetch(self):
            remote = self._selected_remote()
            self._bg(lambda: fetch(self.repo_path, remote),
                     lambda _r: self._show_ok(f"Fetched from {remote}."),
                     busy=f"Fetching from {remote}…", success="Fetched")

        def _on_pull(self):
            remote = self._selected_remote()
            self._bg(lambda: pull(self.repo_path, remote),
                     lambda _r: (self._show_ok(f"Pulled from {remote}."),
                                 self.set_repo(self.repo_path)),
                     busy=f"Pulling from {remote}…", success="Pulled")

        def _on_push(self):
            remote = self._selected_remote()
            self._bg(lambda: push(self.repo_path, remote),
                     lambda _r: self._show_ok(f"Pushed to {remote}."),
                     busy=f"Pushing to {remote}…", success="Pushed")

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server), it prints a friendly note and returns 0
    instead of raising, so ``gui.main()`` is safe to call anywhere.
    """
    try:
        import tkinter as tk
    except Exception as exc:  # tkinter missing entirely
        print(f"{APP_NAME}: a graphical environment with tkinter is required "
              f"to run the GUI ({exc}).")
        return 0

    try:
        App = build_app()
        app = App()
    except tk.TclError as exc:
        # Typically "no display name and no $DISPLAY environment variable".
        print(f"{APP_NAME}: no graphical display available — cannot start the "
              f"GUI here ({exc}). This app is intended for the Windows desktop.")
        return 0
    except Exception as exc:
        print(f"{APP_NAME}: could not start the GUI ({exc}).")
        return 1

    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
