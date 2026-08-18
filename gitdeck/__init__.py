"""gitdeck -- a friendly, pure-Python Git library behind the GitDeck GUI.

All Git operations are implemented on top of `dulwich <https://dulwich.io>`_
(a pure-Python, Apache-2.0 Git implementation) -- gitdeck never shells out to a
system ``git`` binary, so it works even where none is installed.  Every public
function takes an explicit repository path and raises :class:`GitDeckError` on
failure::

    from gitdeck import init_repo, stage, commit, status
    init_repo("/tmp/demo")
    stage("/tmp/demo", ["a.txt"])
    commit("/tmp/demo", "first", author="Ada <ada@example.com>")

The GUI (:mod:`gitdeck.gui`) and CLI (:mod:`gitdeck.__main__`) both build on
this module and never re-implement Git logic.

100% AI-built, open source, published on QuickOpen (quickopen.ai).
"""

from __future__ import annotations

from .errors import GitDeckError
from .repo import open_repo, init_repo, status, current_branch, is_repo, Status
from .stage import stage, unstage, commit
from .history import log, commit_graph
from .diff import file_diff, commit_diff
from .branches import (
    branches,
    create_branch,
    checkout,
    merge,
    delete_branch,
    MergeResult,
)
from .remotes import remotes, add_remote, fetch, pull, push

__version__ = "1.0.6"

__all__ = [
    "GitDeckError",
    "open_repo",
    "init_repo",
    "status",
    "current_branch",
    "is_repo",
    "Status",
    "stage",
    "unstage",
    "commit",
    "log",
    "commit_graph",
    "file_diff",
    "commit_diff",
    "branches",
    "create_branch",
    "checkout",
    "merge",
    "delete_branch",
    "MergeResult",
    "remotes",
    "add_remote",
    "fetch",
    "pull",
    "push",
    "__version__",
]
