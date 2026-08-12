"""Staging the index and creating commits.

``stage`` / ``unstage`` move files into and out of the index (``git add`` /
``git reset <file>``); ``commit`` records the staged snapshot.  Unstaging has no
single dulwich porcelain, so it is done by resetting each index entry back to
its HEAD version (or dropping it when the file is newly added).
"""

from __future__ import annotations

import os
import time

from dulwich import porcelain
from dulwich.index import index_entry_from_tree_entry
from dulwich.object_store import tree_lookup_path

from .errors import GitDeckError
from .repo import open_repo, status

DEFAULT_AUTHOR = "GitDeck User <gitdeck@localhost>"


def _as_list(files):
    if files is None:
        return []
    if isinstance(files, (str, bytes)):
        return [files]
    return list(files)


def _abspath(repo_path, f):
    f = os.fspath(f)
    return f if os.path.isabs(f) else os.path.join(repo_path, f)


def _relbytes(repo_path, f):
    f = os.fspath(f)
    if os.path.isabs(f):
        f = os.path.relpath(f, repo_path)
    return f.replace(os.sep, "/").encode("utf-8")


def stage(path, files):
    """Stage *files* (add/update them in the index). Accepts str or a list."""
    repo = open_repo(path)
    try:
        paths = [_abspath(repo.path, f) for f in _as_list(files)]
        if not paths:
            return
        try:
            porcelain.add(repo, paths=paths)
        except Exception as exc:
            raise GitDeckError(f"Could not stage files: {exc}")
    finally:
        repo.close()


def unstage(path, files):
    """Unstage *files* -- reset each index entry to HEAD (or remove if new)."""
    repo = open_repo(path)
    try:
        rels = [_relbytes(repo.path, f) for f in _as_list(files)]
        if not rels:
            return
        try:
            head_tree = repo[repo.head()].tree
        except KeyError:
            head_tree = None  # unborn branch: nothing committed yet

        try:
            index = repo.open_index()
            for rel in rels:
                mode_sha = None
                if head_tree is not None:
                    try:
                        mode_sha = tree_lookup_path(
                            repo.object_store.__getitem__, head_tree, rel
                        )
                    except KeyError:
                        mode_sha = None
                if mode_sha is not None:
                    mode, sha = mode_sha
                    index[rel] = index_entry_from_tree_entry(mode, sha)
                elif rel in index:
                    del index[rel]
            index.write()
        except GitDeckError:
            raise
        except Exception as exc:
            raise GitDeckError(f"Could not unstage files: {exc}")
    finally:
        repo.close()


def _encode_author(author):
    author = (author or DEFAULT_AUTHOR).strip() or DEFAULT_AUTHOR
    return author.encode("utf-8")


def commit(path, message, author=None, committer=None, timestamp=None, timezone=0):
    """Commit the staged snapshot and return the new commit SHA (hex str).

    *author* / *committer* are ``"Name <email>"`` strings.  Pass *timestamp*
    (epoch seconds) and *timezone* (seconds east of UTC) for a deterministic,
    reproducible commit; otherwise the current time is used.
    """
    if not message or not str(message).strip():
        raise GitDeckError("A commit message is required.")

    repo = open_repo(path)
    try:
        # Refuse an empty commit when there is already history and nothing staged.
        snap = status(path)
        if not snap.staged:
            has_head = True
            try:
                repo.head()
            except KeyError:
                has_head = False
            if has_head:
                raise GitDeckError("Nothing staged to commit.")

        author_b = _encode_author(author)
        committer_b = _encode_author(committer) if committer else author_b
        ts = int(timestamp) if timestamp is not None else int(time.time())
        tz = int(timezone)
        try:
            sha = porcelain.commit(
                repo,
                message=str(message).encode("utf-8"),
                author=author_b,
                committer=committer_b,
                author_timestamp=ts,
                author_timezone=tz,
                commit_timestamp=ts,
                commit_timezone=tz,
            )
        except GitDeckError:
            raise
        except Exception as exc:
            raise GitDeckError(f"Could not create commit: {exc}")
        return sha.decode("ascii") if isinstance(sha, bytes) else str(sha)
    finally:
        repo.close()
