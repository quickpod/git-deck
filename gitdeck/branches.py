"""Branch operations: list, create, checkout, merge and delete.

Merges use dulwich's in-process 3-way merge.  On conflict we do not raise --
we return a :class:`MergeResult` whose ``status`` is ``"conflict"`` and whose
``conflicts`` lists the affected paths, so the caller can show a clear message.
"""

from __future__ import annotations

from dulwich import porcelain

from .errors import GitDeckError
from .repo import open_repo, current_branch, _decode
from .stage import DEFAULT_AUTHOR, _encode_author

import os
from collections import namedtuple

# status: "up_to_date" | "merged" | "conflict"
MergeResult = namedtuple("MergeResult", "status sha conflicts")


def branches(path):
    """Return the repository's local branch names as a sorted list of str."""
    repo = open_repo(path)
    try:
        try:
            names = porcelain.branch_list(repo)
        except Exception as exc:
            raise GitDeckError(f"Could not list branches: {exc}")
        return sorted(_decode(n) for n in names)
    finally:
        repo.close()


def create_branch(path, name, base=None):
    """Create a new branch *name* (from *base*, default HEAD). Does not switch."""
    if not name or not str(name).strip():
        raise GitDeckError("A branch name is required.")
    repo = open_repo(path)
    try:
        try:
            porcelain.branch_create(repo, str(name), objectish=base)
        except Exception as exc:
            raise GitDeckError(f"Could not create branch {name!r}: {exc}")
    finally:
        repo.close()


def _worktree_content_dirty(repo):
    """True if any indexed file's *content* differs from the index.

    Dulwich's checkout safety check stat-compares, and on Windows file-mode
    semantics make clean files look modified (the ``core.filemode=false``
    problem). Comparing blob hashes ignores stat noise.
    """
    from dulwich.objects import Blob

    index = repo.open_index()
    root = repo.path if isinstance(repo.path, str) else repo.path.decode()
    for rel, entry in index.items():
        fp = os.path.join(root, rel.decode("utf-8", "replace"))
        try:
            with open(fp, "rb") as fh:
                data = fh.read()
        except OSError:
            return True  # deleted/unreadable counts as dirty
        if Blob.from_string(data).id != entry.sha:
            return True
    return False


def checkout(path, name, force=False):
    """Switch the working tree to branch (or commit-ish) *name*."""
    if not name or not str(name).strip():
        raise GitDeckError("A branch name is required.")
    repo = open_repo(path)
    try:
        # Decide force up-front: dulwich's checkout safety check stat-compares,
        # and on Windows file-mode semantics make clean tracked files look
        # modified ("would be overwritten by checkout"). If nothing differs by
        # content-hash the tree is clean, so a forced checkout is safe; real
        # uncommitted content still leaves it dirty and blocks (below).
        eff_force = force
        if not eff_force:
            try:
                eff_force = not _worktree_content_dirty(repo)
            except Exception:
                eff_force = False
        try:
            porcelain.checkout(repo, str(name), force=eff_force)
        except Exception as exc:
            raise GitDeckError(
                f"Cannot switch to {name!r}: {exc}. Commit or stash your changes "
                f"first (or force the checkout)."
            )
    finally:
        repo.close()


def delete_branch(path, name):
    """Delete local branch *name* (refuses to delete the current branch)."""
    if not name or not str(name).strip():
        raise GitDeckError("A branch name is required.")
    if str(name) == current_branch(path):
        raise GitDeckError(
            f"Cannot delete {name!r}: it is the current branch. Switch away first."
        )
    repo = open_repo(path)
    try:
        try:
            porcelain.branch_delete(repo, str(name))
        except Exception as exc:
            raise GitDeckError(f"Could not delete branch {name!r}: {exc}")
    finally:
        repo.close()


def merge(path, branch, author=None, committer=None):
    """Merge *branch* into the current branch.

    Returns a :class:`MergeResult`.  ``status`` is ``"up_to_date"`` when there
    was nothing to do, ``"merged"`` on success (fast-forward or a new merge
    commit) and ``"conflict"`` when files could not be merged automatically.
    """
    if not branch or not str(branch).strip():
        raise GitDeckError("A branch to merge is required.")
    repo = open_repo(path)
    try:
        author_b = _encode_author(author)
        committer_b = _encode_author(committer) if committer else author_b
        try:
            merge_commit, conflicts = porcelain.merge(
                repo,
                str(branch),
                author=author_b,
                committer=committer_b,
            )
        except Exception as exc:
            raise GitDeckError(f"Could not merge {branch!r}: {exc}")

        conflict_paths = [_decode(c) for c in (conflicts or [])]
        if conflict_paths:
            return MergeResult("conflict", None, conflict_paths)
        if merge_commit is None:
            return MergeResult("up_to_date", None, [])
        sha = _decode(merge_commit)
        return MergeResult("merged", sha, [])
    finally:
        repo.close()
