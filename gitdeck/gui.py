#!/usr/bin/env python3
r"""GitDeck -- an Aura (QuickOpen design system) GUI on top of the ``gitdeck`` library.

A single Aura window with a sidebar of sections -- **Changes** (stage / diff /
commit), **History** (a live commit-graph canvas + per-commit diff), **Branches**
(create / checkout / merge / delete) and **Remotes** (add + fetch / pull / push),
plus an **About** panel.  Every operation calls the tested core library (never
re-implements Git logic, always via pure-Python dulwich -- never shelling out to
a ``git`` binary); anything that touches the network (fetch/pull/push) runs on a
background thread and is marshalled back with ``self.after``.  Failures are shown
in the Aura status bar as the :class:`GitDeckError` message -- never a traceback.

Design goals baked in here (mirroring the QuickOpen house style):
  * built on the vendored ``gitdeck/aura.py`` design system, which layers the
    quickopen.ai look (deep space + light) over CustomTkinter.  Runtime deps:
    ``customtkinter`` (+ ``darkdetect``) -- declared in requirements.txt; the
    PyInstaller build adds ``--collect-all customtkinter``.
  * Importing this module does nothing.  Only :func:`main` builds a root window,
    and it degrades gracefully (prints a message, returns 0) with no display or
    with customtkinter missing.
  * Frozen-exe safe: bundled assets are resolved via ``sys._MEIPASS`` / the exe
    directory when ``sys.frozen`` is set -- never ``__file__``.
  * The diff/commit-message viewers are raw ``tk.Text`` widgets and the history
    graph is a raw ``tk.Canvas``, all registered with ``aura.track`` so they
    re-theme when the sidebar toggle flips dark<->light; the graph re-renders on
    the flip so its accent-coloured nodes/edges follow the theme.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

import os
import sys
import threading

# tkinter/customtkinter are imported lazily inside main()/build_app so that
# merely importing this module (e.g. during packaging or on a headless CI box)
# never fails.

APP_NAME = "GitDeck"
APP_VERSION = "1.0.0"
WINDOW_TITLE = "GitDeck — by QuickOpen (quickopen.ai)"
PROJECT_URL = "https://quickopen.ai"
ACCENT = "#cf2d3a"      # publish/specs/git-deck.json "accent": [207, 45, 58]

DEFAULT_AUTHOR = "GitDeck User <gitdeck@localhost>"

VIEWS = [
    ("changes", "Changes"),
    ("history", "History"),
    ("branches", "Branches"),
    ("remotes", "Remotes"),
    ("about", "About"),
]


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
# The app (built lazily; tkinter/customtkinter imported only inside build_app)
# ---------------------------------------------------------------------------
def build_app():
    """Construct and return the App class bound to live GUI imports.

    Kept inside a function so this module imports cleanly without a display
    (and without customtkinter installed).
    """
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
    import customtkinter as ctk

    from . import aura, guiconfig
    from .errors import GitDeckError
    # These names are re-exported *functions* from gitdeck/__init__ (the package
    # attribute is the function, not a module) -- import them directly.
    from . import (
        is_repo, init_repo, status, current_branch,
        commit_graph, file_diff, commit_diff,
        stage, unstage, commit,
        branches, create_branch, checkout, merge, delete_branch,
        remotes, add_remote, fetch, pull, push,
    )

    FONT = "Segoe UI" if os.name == "nt" else "DejaVu Sans"
    MONO = "Consolas" if os.name == "nt" else "DejaVu Sans Mono"

    class App(aura.AuraApp):
        def __init__(self):
            super().__init__(
                title=WINDOW_TITLE, app_name=APP_NAME, accent=ACCENT,
                theme=guiconfig.get_theme(),
                icon_png=asset_path("git-deck.png"), version=APP_VERSION,
                tagline="visual git",
                on_theme_change=guiconfig.set_theme,
                size=(1160, 740), min_size=(940, 600))

            self.repo_path = None
            self._busy = False
            self._img_refs = []
            self._diff_texts = []        # tk.Text panes needing diff re-tagging
            self._graph = None           # last commit_graph() result
            self._graph_canvas = None
            self._selected_row = None

            self._set_icon()
            self._build_header_extras()
            self._build_menu()
            self.add_section("changes", "Changes", "✎", self._build_changes)
            self.add_section("history", "History", "◷", self._build_history)
            self.add_section("branches", "Branches", "◈", self._build_branches)
            self.add_section("remotes", "Remotes", "⇄", self._build_remotes)
            self.add_section("about", "About", "ℹ", self._build_about)
            # per-section repopulate hooks (used by refresh / opening a repo)
            self._section_refresh = {
                "changes": self._refresh_changes,
                "history": self._load_history,
                "branches": self._refresh_branches,
                "remotes": self._refresh_remotes,
            }
            self.show("changes")
            self.set_status("Ready")
            self.protocol("WM_DELETE_WINDOW", self.destroy)
            self.after(50, self._open_last_or_prompt)

        # ---- assets / icon -------------------------------------------------
        def _set_icon(self):
            try:
                ico = asset_path("git-deck.ico")
                if ico and os.name == "nt":
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

        # ---- header (repo indicator + Open Repo action) --------------------
        def _build_header_extras(self):
            self._repo_caption = aura.Caption(self.header_actions,
                                              "No repository open")
            self._repo_caption.pack(side="left", padx=(0, 10))
            aura.AuraButton(self.header_actions, "Open repo…", kind="secondary",
                            height=30,
                            command=self._open_repo_dialog).pack(side="left")

        # ---- theme flip: re-colour diff tags + redraw the graph ------------
        def set_theme(self, theme):
            super().set_theme(theme)
            for txt in list(self._diff_texts):
                self._retag_diff(txt)
            try:
                if (self._graph_canvas is not None
                        and self._graph_canvas.winfo_exists()
                        and self._graph is not None):
                    self._render_graph()
            except Exception:
                pass

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
            viewm.add_command(
                label="Toggle dark mode",
                command=lambda: self.set_theme(
                    "light" if self.theme == "dark" else "dark"))
            viewm.add_command(label="Refresh", accelerator="F5",
                              command=self.refresh)
            bar.add_cascade(label="View", menu=viewm)

            helpm = tk.Menu(bar, tearoff=0)
            helpm.add_command(label="About", command=lambda: self.show("about"))
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

        # ---- status helpers (Aura StatusBar is the single voice) -----------
        def _set_status(self, text, kind="idle"):
            self.set_status(text, kind)

        def _show_error(self, message):
            self.set_error(str(message))

        def _show_ok(self, message):
            self.set_success(str(message))

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
                self._show_error(ex)
                return None
            except Exception as ex:
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
            self.set_status("Open a repository to get started "
                            "(File → Open Repository).")

        def _open_repo_dialog(self):
            path = filedialog.askdirectory(title="Open a Git repository")
            if not path:
                return
            if not is_repo(path):
                if messagebox.askyesno(
                        "Not a repository",
                        f"{path} is not a Git repository.\n\nInitialise one here?"):
                    if self._do(lambda: init_repo(path),
                                success="Initialised") is None:
                        return
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
                branch = f"  ·  on {cb}" if cb else ""
            except Exception:
                pass
            self._repo_caption.configure(
                text=f"{os.path.basename(os.path.normpath(path))}{branch}")
            self.set_status(f"{path}{branch}")
            self.refresh()

        def refresh(self):
            """Repopulate every already-built section from the current repo."""
            if not self.repo_path:
                return
            for sid, fn in self._section_refresh.items():
                sec = self._sections.get(sid)
                if sec and sec.get("built"):
                    fn()

        # ---- raw tk.Text diff pane (tracked + diff-tagged) ----------------
        def _retag_diff(self, txt):
            try:
                if not txt.winfo_exists():
                    return
                txt.tag_configure("add", foreground=aura.P("ok"))
                txt.tag_configure("del", foreground=aura.P("danger"))
                txt.tag_configure("hunk", foreground=aura.P("accent"))
            except Exception:
                pass

        def _text_pane(self, parent, height=12):
            frame = ctk.CTkFrame(parent, fg_color="transparent")
            txt = tk.Text(frame, height=height, wrap="none", font=(MONO, 10),
                          borderwidth=0)
            sb = ttk.Scrollbar(frame, orient="vertical", command=txt.yview)
            sbx = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
            txt.configure(yscrollcommand=sb.set, xscrollcommand=sbx.set,
                          state="disabled")
            sb.pack(side="right", fill="y")
            sbx.pack(side="bottom", fill="x")
            txt.pack(side="left", fill="both", expand=True)
            aura.track(txt, "text")
            self._diff_texts.append(txt)
            self._retag_diff(txt)
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

        # =================================================================
        # Changes section
        # =================================================================
        def _build_changes(self, frame):
            aura.Caption(frame,
                         "Stage changes, review the diff, then commit.").pack(
                anchor="w", pady=(0, 10))

            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True)

            lists = ctk.CTkFrame(body, fg_color="transparent")
            lists.pack(side="left", fill="y")

            aura.SectionLabel(lists, "STAGED").pack(anchor="w")
            self._staged_lb = tk.Listbox(lists, height=8, width=34,
                                         selectmode="extended",
                                         exportselection=False)
            self._staged_lb.pack(fill="x", pady=(2, 0))
            aura.track(self._staged_lb, "listbox")
            aura.AuraButton(lists, "Unstage selected", kind="secondary",
                            command=self._on_unstage).pack(fill="x", pady=(6, 12))

            aura.SectionLabel(lists, "UNSTAGED / UNTRACKED").pack(anchor="w")
            self._unstaged_lb = tk.Listbox(lists, height=10, width=34,
                                           selectmode="extended",
                                           exportselection=False)
            self._unstaged_lb.pack(fill="x", pady=(2, 0))
            aura.track(self._unstaged_lb, "listbox")
            stage_row = ctk.CTkFrame(lists, fg_color="transparent")
            stage_row.pack(fill="x", pady=(6, 0))
            aura.AuraButton(stage_row, "Stage selected", kind="secondary",
                            command=self._on_stage).pack(side="left")
            aura.AuraButton(stage_row, "Stage all", kind="secondary",
                            command=self._on_stage_all).pack(
                side="left", padx=(8, 0))

            right = ctk.CTkFrame(body, fg_color="transparent")
            right.pack(side="left", fill="both", expand=True, padx=(16, 0))
            diff_frame, self._changes_diff = self._text_pane(right, height=12)
            diff_frame.pack(fill="both", expand=True)

            commit_box = aura.Card(right, title="Commit")
            commit_box.pack(fill="x", pady=(12, 0))
            self._commit_msg = tk.Text(commit_box.body, height=3, wrap="word",
                                       font=(FONT, 10), borderwidth=0)
            self._commit_msg.pack(fill="x")
            aura.track(self._commit_msg, "text")
            row = ctk.CTkFrame(commit_box.body, fg_color="transparent")
            row.pack(fill="x", pady=(8, 0))
            aura.Caption(row, "Author").pack(side="left", padx=(0, 6))
            # No textvariable: keep it a plain entry pre-filled with the default.
            self._author_entry = aura.AuraEntry(row)
            self._author_entry.insert(0, DEFAULT_AUTHOR)
            self._author_entry.pack(side="left", fill="x", expand=True,
                                    padx=(0, 8))
            aura.AuraButton(row, "Commit", kind="primary",
                            command=self._on_commit).pack(side="right")

            for lb in (self._staged_lb, self._unstaged_lb):
                lb.bind("<<ListboxSelect>>", self._on_change_select)
            self._refresh_changes()

        def _refresh_changes(self):
            if not self.repo_path:
                return
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
            author = self._author_entry.get().strip() or DEFAULT_AUTHOR
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
                self._load_history()

        # =================================================================
        # History section (commit-graph canvas + per-commit diff)
        # =================================================================
        def _build_history(self, frame):
            aura.Caption(frame,
                         "Browse commits as a graph; select one to see what "
                         "it changed.").pack(anchor="w", pady=(0, 10))

            graph_frame = ctk.CTkFrame(frame, fg_color="transparent")
            graph_frame.pack(fill="both", expand=True)
            canvas = tk.Canvas(graph_frame, highlightthickness=0, bd=0)
            sb = ttk.Scrollbar(graph_frame, orient="vertical",
                               command=canvas.yview)
            canvas.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            canvas.pack(side="left", fill="both", expand=True)
            aura.track(canvas, "canvas")
            canvas.bind("<Button-1>", self._on_graph_click)
            self._graph_canvas = canvas

            diff_frame, self._history_diff = self._text_pane(frame, height=12)
            diff_frame.pack(fill="both", expand=True, pady=(12, 0))

            self._load_history()

        def _load_history(self):
            if not getattr(self, "_graph_canvas", None):
                return
            if not self.repo_path:
                self._graph = None
                self._selected_row = None
                try:
                    self._graph_canvas.delete("all")
                    self._set_diff(self._history_diff, "")
                except Exception:
                    pass
                return
            graph = self._do(lambda: commit_graph(self.repo_path, max=200))
            if graph is None:
                return
            self._graph = graph
            self._selected_row = None
            self._render_graph()
            if not graph["nodes"]:
                self._set_diff(self._history_diff, "")

        # Layout constants for the graph rail.
        _ROW_H = 26
        _X0 = 18
        _LANE_W = 16
        _DOT_R = 4

        def _render_graph(self):
            c = self._graph_canvas
            c.delete("all")
            graph = self._graph or {"nodes": [], "edges": []}
            nodes = graph["nodes"]
            row_h, x0, lane_w, r = (self._ROW_H, self._X0, self._LANE_W,
                                    self._DOT_R)
            accent = aura.P("accent")
            surface = aura.P("surface")
            text_c = aura.P("text")
            muted = aura.P("muted")
            faint = aura.P("faint")
            edge_c = aura.mix(accent, aura.P("bg"), 0.45)

            pos = {n["sha"]: (n["lane"], i) for i, n in enumerate(nodes)}
            max_lane = max((n["lane"] for n in nodes), default=0)
            text_x = x0 + (max_lane + 1) * lane_w + 12
            big_w = max(2000, c.winfo_width())

            # selection highlight (behind everything)
            if (self._selected_row is not None
                    and 0 <= self._selected_row < len(nodes)):
                y = self._selected_row * row_h
                c.create_rectangle(0, y, big_w, y + row_h,
                                   fill=aura.P("accent_soft"), width=0)

            # edges (child -> parent), drawn under the nodes
            for child, parent in graph["edges"]:
                if child in pos and parent in pos:
                    cl, ci = pos[child]
                    pl, pi = pos[parent]
                    x1 = x0 + cl * lane_w
                    y1 = ci * row_h + row_h // 2
                    x2 = x0 + pl * lane_w
                    y2 = pi * row_h + row_h // 2
                    c.create_line(x1, y1, x2, y2, fill=edge_c, width=2)

            # nodes + text
            for i, node in enumerate(nodes):
                lane = node["lane"]
                x = x0 + lane * lane_w
                y = i * row_h + row_h // 2
                c.create_oval(x - r, y - r, x + r, y + r,
                              fill=accent, outline=surface, width=1)
                c.create_text(text_x, y, anchor="w", text=node["sha"][:7],
                              fill=muted, font=(MONO, 9))
                summary = (node.get("summary") or "").strip()
                if len(summary) > 60:
                    summary = summary[:59] + "…"
                sid = c.create_text(text_x + 62, y, anchor="w", text=summary,
                                    fill=text_c, font=(FONT, 9))
                author = (node.get("author") or "").split("<")[0].strip()
                date = (node.get("date") or "")[:16]
                meta = f"   {author}  ·  {date}".rstrip()
                bbox = c.bbox(sid)
                meta_x = (bbox[2] + 14) if bbox else text_x + 200
                c.create_text(meta_x, y, anchor="w", text=meta,
                              fill=faint, font=(FONT, 9))

            c.configure(scrollregion=(0, 0, big_w, max(1, len(nodes)) * row_h))

        def _on_graph_click(self, event):
            if not self._graph or not self._graph["nodes"]:
                return
            row = int(self._graph_canvas.canvasy(event.y) // self._ROW_H)
            nodes = self._graph["nodes"]
            if not (0 <= row < len(nodes)):
                return
            self._selected_row = row
            self._render_graph()
            sha = nodes[row]["sha"]
            text = self._do(lambda: commit_diff(self.repo_path, sha))
            self._set_diff(self._history_diff, text or "")

        # =================================================================
        # Branches section
        # =================================================================
        def _build_branches(self, frame):
            aura.Caption(frame,
                         "Create, switch, merge and delete local branches.").pack(
                anchor="w", pady=(0, 10))

            body = ctk.CTkFrame(frame, fg_color="transparent")
            body.pack(fill="both", expand=True)

            self._branch_lb = tk.Listbox(body, height=14, width=36,
                                         exportselection=False)
            self._branch_lb.pack(side="left", fill="y")
            aura.track(self._branch_lb, "listbox")

            btns = ctk.CTkFrame(body, fg_color="transparent")
            btns.pack(side="left", fill="y", padx=(16, 0))

            new_row = ctk.CTkFrame(btns, fg_color="transparent")
            new_row.pack(fill="x")
            self._new_branch_entry = aura.AuraEntry(
                new_row, placeholder="New branch name…", width=200)
            self._new_branch_entry.pack(side="left", padx=(0, 8))
            aura.AuraButton(new_row, "Create", kind="primary",
                            command=self._on_create_branch).pack(side="left")

            aura.AuraButton(btns, "Checkout selected", kind="secondary",
                            command=self._on_checkout).pack(fill="x", pady=(12, 0))
            aura.AuraButton(btns, "Merge selected → current", kind="secondary",
                            command=self._on_merge).pack(fill="x", pady=(8, 0))
            aura.AuraButton(btns, "Delete selected", kind="danger",
                            command=self._on_delete_branch).pack(
                fill="x", pady=(8, 0))

            self._refresh_branches()

        def _refresh_branches(self):
            if not self.repo_path:
                return
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
            name = self._new_branch_entry.get().strip()
            if not name:
                self._show_error("Enter a branch name first.")
                return
            if self._do(lambda: create_branch(self.repo_path, name),
                        success="Branch created") is not None:
                self._new_branch_entry.delete(0, "end")
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
            author = getattr(self, "_author_entry", None)
            author = author.get().strip() if author else DEFAULT_AUTHOR
            result = self._do(
                lambda: merge(self.repo_path, name, author=author or DEFAULT_AUTHOR))
            if result is None:
                return
            if result.status == "conflict":
                self._show_error(
                    "Merge conflict in: " + ", ".join(result.conflicts) +
                    " — resolve, stage and commit to finish.")
            elif result.status == "up_to_date":
                self._show_ok("Already up to date.")
            else:
                self._show_ok(f"Merged {name} ({result.sha[:7]}).")
            self._refresh_branches()
            self._load_history()

        # =================================================================
        # Remotes section
        # =================================================================
        def _build_remotes(self, frame):
            aura.Caption(frame,
                         "Manage remotes and sync. Network operations run in "
                         "the background.").pack(anchor="w", pady=(0, 10))

            cols = ("name", "url")
            tree = ttk.Treeview(frame, columns=cols, show="headings", height=8,
                                selectmode="browse")
            tree.heading("name", text=aura.spaced("Name"), anchor="w")
            tree.heading("url", text=aura.spaced("URL"), anchor="w")
            tree.column("name", width=140, anchor="w")
            tree.column("url", width=560, anchor="w")
            tree.pack(fill="x")
            self._remote_tree = tree

            add_row = ctk.CTkFrame(frame, fg_color="transparent")
            add_row.pack(fill="x", pady=(12, 0))
            aura.Caption(add_row, "Name").pack(side="left", padx=(0, 4))
            self._rname_entry = aura.AuraEntry(add_row, width=120)
            self._rname_entry.insert(0, "origin")
            self._rname_entry.pack(side="left", padx=(0, 10))
            aura.Caption(add_row, "URL").pack(side="left", padx=(0, 4))
            self._rurl_entry = aura.AuraEntry(
                add_row, placeholder="https://example.com/repo.git")
            self._rurl_entry.pack(side="left", fill="x", expand=True,
                                  padx=(0, 10))
            aura.AuraButton(add_row, "Add remote", kind="secondary",
                            command=self._on_add_remote).pack(side="left")

            ops = ctk.CTkFrame(frame, fg_color="transparent")
            ops.pack(fill="x", pady=(12, 0))
            aura.AuraButton(ops, "Fetch", kind="secondary",
                            command=self._on_fetch).pack(side="left")
            aura.AuraButton(ops, "Pull", kind="secondary",
                            command=self._on_pull).pack(side="left", padx=(8, 0))
            aura.AuraButton(ops, "Push", kind="primary",
                            command=self._on_push).pack(side="left", padx=(8, 0))

            self._refresh_remotes()

        def _refresh_remotes(self):
            if not self.repo_path:
                return
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
            return self._rname_entry.get().strip() or "origin"

        def _on_add_remote(self):
            name = self._rname_entry.get().strip()
            url = self._rurl_entry.get().strip()
            if self._do(lambda: add_remote(self.repo_path, name, url),
                        success="Remote added") is not None:
                self._rurl_entry.delete(0, "end")
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

        # =================================================================
        # About section
        # =================================================================
        def _build_about(self, frame):
            card = aura.Card(frame, title="About GitDeck")
            card.pack(fill="x")
            aura.Heading(card.body, APP_NAME).pack(anchor="w")
            aura.Caption(card.body, f"Version {APP_VERSION}").pack(
                anchor="w", pady=(0, 10))
            ctk.CTkLabel(
                card.body, font=aura.font(), justify="left", anchor="w",
                wraplength=560,
                text="A friendly, fully-offline Git client: stage and commit "
                     "with a live diff, browse history as a commit graph, "
                     "create / switch / merge / delete branches, and manage "
                     "remotes with background fetch / pull / push.\n\n"
                     "Built on pure-Python dulwich — GitDeck never shells out "
                     "to a system git binary, so it works even where none is "
                     "installed. 100% AI-built, open source, published on "
                     "QuickOpen.").pack(anchor="w")
            aura.Caption(card.body,
                         "Licensed under Apache-2.0. Built on dulwich "
                         "(Apache-2.0) and CustomTkinter (MIT).").pack(
                anchor="w", pady=(10, 4))
            aura.AuraButton(card.body, "Project page: quickopen.ai",
                            kind="ghost",
                            command=lambda: open_with_default_app(
                                PROJECT_URL)).pack(anchor="w", pady=(6, 0))

    return App


def main():
    """Entry point: build the root window and run.  Degrades on headless hosts.

    Importing this module does nothing; only this function creates a Tk root.
    With no display (e.g. a server) or without customtkinter installed, it
    prints a friendly note and returns 0 instead of raising, so ``gui.main()``
    is safe to call anywhere.
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
    except ImportError as exc:
        print(f"{APP_NAME}: the GUI needs the 'customtkinter' package "
              f"({exc}). Install it with:  pip install customtkinter")
        return 0
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
