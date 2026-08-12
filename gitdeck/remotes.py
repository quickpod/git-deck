"""Remotes: list/add plus the network operations fetch, pull and push.

The network operations are thin wrappers over dulwich that turn any failure --
a bad URL, an auth prompt, no connectivity -- into a clear :class:`GitDeckError`
rather than a traceback.  The GUI always runs these on a background thread; the
test-suite never touches the network.
"""

from __future__ import annotations

from io import BytesIO

from dulwich import porcelain

from .errors import GitDeckError
from .repo import open_repo, _decode


def remotes(path):
    """Return configured remotes as a sorted list of ``{"name", "url"}`` dicts."""
    repo = open_repo(path)
    try:
        out = []
        config = repo.get_config()
        for section in config.sections():
            if len(section) == 2 and section[0] == b"remote":
                name = _decode(section[1])
                try:
                    url = _decode(config.get(section, b"url"))
                except KeyError:
                    url = ""
                out.append({"name": name, "url": url})
        return sorted(out, key=lambda r: r["name"])
    finally:
        repo.close()


def add_remote(path, name, url):
    """Add a remote *name* pointing at *url*."""
    if not name or not str(name).strip():
        raise GitDeckError("A remote name is required.")
    if not url or not str(url).strip():
        raise GitDeckError("A remote URL is required.")
    repo = open_repo(path)
    try:
        try:
            porcelain.remote_add(repo, str(name), str(url))
        except Exception as exc:
            raise GitDeckError(f"Could not add remote {name!r}: {exc}")
    finally:
        repo.close()


def _resolve_remote(repo, remote):
    if remote and str(remote).strip():
        return str(remote)
    return "origin"


def fetch(path, remote="origin"):
    """Fetch refs and objects from *remote* (network)."""
    repo = open_repo(path)
    try:
        try:
            errstream = BytesIO()
            porcelain.fetch(repo, _resolve_remote(repo, remote), errstream=errstream)
        except Exception as exc:
            raise GitDeckError(f"Fetch from {remote!r} failed: {exc}")
    finally:
        repo.close()


def pull(path, remote="origin", refspecs=None):
    """Pull (fetch + merge) from *remote* (network)."""
    repo = open_repo(path)
    try:
        try:
            errstream = BytesIO()
            porcelain.pull(
                repo,
                _resolve_remote(repo, remote),
                refspecs=refspecs,
                errstream=errstream,
            )
        except Exception as exc:
            raise GitDeckError(f"Pull from {remote!r} failed: {exc}")
    finally:
        repo.close()


def push(path, remote="origin", refspecs=None):
    """Push local commits to *remote* (network)."""
    repo = open_repo(path)
    try:
        try:
            errstream = BytesIO()
            porcelain.push(
                repo,
                _resolve_remote(repo, remote),
                refspecs=refspecs,
                errstream=errstream,
            )
        except Exception as exc:
            raise GitDeckError(f"Push to {remote!r} failed: {exc}")
    finally:
        repo.close()
