"""Unified diffs for working-tree files and for whole commits."""

from __future__ import annotations

import os
from io import BytesIO

from dulwich import porcelain
from dulwich.patch import write_tree_diff

from .errors import GitDeckError
from .repo import open_repo


def _relbytes(repo_path, f):
    f = os.fspath(f)
    if os.path.isabs(f):
        f = os.path.relpath(f, repo_path)
    return f.replace(os.sep, "/").encode("utf-8")


def file_diff(path, file, staged=False):
    """Return the unified diff for a single *file* as text.

    With ``staged=False`` this is the working-tree change (index vs working
    copy); with ``staged=True`` it is the staged change (HEAD vs index).
    Returns an empty string when there is no difference.
    """
    repo = open_repo(path)
    try:
        rel = _relbytes(repo.path, file)
        buf = BytesIO()
        try:
            porcelain.diff(repo, paths=[rel], staged=staged, outstream=buf)
        except Exception as exc:
            raise GitDeckError(f"Could not diff {os.fspath(file)!r}: {exc}")
        return buf.getvalue().decode("utf-8", "replace")
    finally:
        repo.close()


def commit_diff(path, sha):
    """Return the unified diff a commit introduced (its parent vs the commit)."""
    repo = open_repo(path)
    try:
        key = sha.encode("ascii") if isinstance(sha, str) else sha
        try:
            commit = repo[key]
        except Exception:
            raise GitDeckError(f"No such commit: {sha!r}")
        new_tree = commit.tree
        old_tree = None
        if commit.parents:
            try:
                old_tree = repo[commit.parents[0]].tree
            except Exception:
                old_tree = None
        buf = BytesIO()
        try:
            write_tree_diff(buf, repo.object_store, old_tree, new_tree)
        except Exception as exc:
            raise GitDeckError(f"Could not diff commit {sha!r}: {exc}")
        return buf.getvalue().decode("utf-8", "replace")
    finally:
        repo.close()
