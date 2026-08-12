"""Repository basics: open/init, working-tree status and the current branch.

Thin, well-guarded wrappers over :mod:`dulwich.porcelain` / :class:`dulwich.repo.Repo`.
Everything here returns plain Python types (``str`` paths, lists of ``str``) and
raises :class:`GitDeckError` -- never a raw dulwich exception -- on failure.
"""

from __future__ import annotations

import os
from collections import namedtuple

from dulwich.repo import Repo
from dulwich import porcelain

from .errors import GitDeckError

# A friendly, decoded snapshot of the working tree.
#   * ``branch``        -- current branch name (str) or None when detached / unborn
#   * ``staged``        -- sorted list of paths staged for the next commit
#   * ``unstaged``      -- tracked files with un-staged modifications/deletions
#   * ``untracked``     -- files git does not know about yet
#   * ``staged_detail`` -- {"add": [...], "modify": [...], "delete": [...]}
Status = namedtuple(
    "Status", "branch staged unstaged untracked staged_detail"
)


def _decode(value):
    """Best-effort decode of a dulwich bytes value to ``str``."""
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.decode("utf-8", "replace")
    return value


def is_repo(path):
    """Return ``True`` if *path* is (inside) a Git repository."""
    try:
        Repo(path).close()
        return True
    except Exception:
        return False


def open_repo(path):
    """Open and return the :class:`dulwich.repo.Repo` at *path*.

    Raises :class:`GitDeckError` if *path* is not a Git repository.
    """
    if not path or not os.path.isdir(path):
        raise GitDeckError(f"No such folder: {path!r}")
    try:
        return Repo(path)
    except Exception:
        raise GitDeckError(
            f"{path!r} is not a Git repository. Open a repository or "
            f"initialise one first."
        )


def init_repo(path):
    """Create a new, empty Git repository at *path* and return its path.

    Creates the folder if needed.  Raises :class:`GitDeckError` on failure.
    """
    try:
        os.makedirs(path, exist_ok=True)
    except OSError as exc:
        raise GitDeckError(f"Could not create {path!r}: {exc}")
    try:
        repo = porcelain.init(path)
        try:
            repo.close()
        except Exception:
            pass
        return path
    except Exception as exc:
        raise GitDeckError(f"Could not initialise a repository at {path!r}: {exc}")


def current_branch(path):
    """Return the current branch name (str), or ``None`` if detached/unborn."""
    repo = open_repo(path)
    try:
        try:
            return _decode(porcelain.active_branch(repo))
        except Exception:
            return None
    finally:
        repo.close()


def status(path):
    """Return a :class:`Status` snapshot of the working tree at *path*."""
    repo = open_repo(path)
    try:
        try:
            gs = porcelain.status(repo, untracked_files="all")
        except Exception as exc:
            raise GitDeckError(f"Could not read repository status: {exc}")

        detail = {
            "add": sorted(_decode(p) for p in gs.staged.get("add", [])),
            "modify": sorted(_decode(p) for p in gs.staged.get("modify", [])),
            "delete": sorted(_decode(p) for p in gs.staged.get("delete", [])),
        }
        staged = sorted(detail["add"] + detail["modify"] + detail["delete"])
        unstaged = sorted(_decode(p) for p in gs.unstaged)
        untracked = sorted(_decode(p) for p in gs.untracked)

        try:
            branch = _decode(porcelain.active_branch(repo))
        except Exception:
            branch = None

        return Status(branch, staged, unstaged, untracked, detail)
    finally:
        repo.close()
