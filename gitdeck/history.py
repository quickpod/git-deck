"""Commit history and a simple commit-graph structure for the History view."""

from __future__ import annotations

from datetime import datetime, timezone, timedelta

from .errors import GitDeckError
from .repo import open_repo, _decode


def _format_date(epoch, tz_seconds):
    try:
        tz = timezone(timedelta(seconds=int(tz_seconds or 0)))
        return datetime.fromtimestamp(int(epoch), tz).strftime("%Y-%m-%d %H:%M:%S %z")
    except Exception:
        return ""


def log(path, max=100):
    """Return up to *max* commits, newest first.

    Each entry is a dict: ``sha`` (hex str), ``author`` (str), ``date`` (str),
    ``timestamp`` (int epoch), ``message`` (str, full), ``summary`` (first line)
    and ``parents`` (list of hex-str parent SHAs).
    """
    repo = open_repo(path)
    try:
        try:
            repo.head()
        except KeyError:
            return []  # unborn branch -- no commits yet
        entries = []
        try:
            walker = repo.get_walker(max_entries=max)
            for entry in walker:
                c = entry.commit
                message = _decode(c.message)
                entries.append(
                    {
                        "sha": _decode(c.id),
                        "author": _decode(c.author),
                        "date": _format_date(c.author_time, c.author_timezone),
                        "timestamp": int(c.author_time),
                        "message": message,
                        "summary": message.splitlines()[0] if message else "",
                        "parents": [_decode(p) for p in c.parents],
                    }
                )
        except Exception as exc:
            raise GitDeckError(f"Could not read history: {exc}")
        return entries
    finally:
        repo.close()


def commit_graph(path, max=100):
    """Return a simple commit-graph structure for drawing a history graph.

    ``{"nodes": [<log entry> + "lane", ...], "edges": [(child_sha, parent_sha)]}``.
    Lanes are a naive assignment: a child keeps its first parent's lane and each
    additional distinct parent claims the next free lane -- enough for a readable
    left-rail graph without a full topological layout engine.
    """
    entries = log(path, max=max)
    nodes = []
    edges = []
    lanes = {}          # sha -> lane index
    free_lane = [0]

    def claim():
        lane = free_lane[0]
        free_lane[0] += 1
        return lane

    for entry in entries:
        sha = entry["sha"]
        lane = lanes.pop(sha, None)
        if lane is None:
            lane = claim()
        node = dict(entry)
        node["lane"] = lane
        nodes.append(node)
        for i, parent in enumerate(entry["parents"]):
            edges.append((sha, parent))
            if parent not in lanes:
                lanes[parent] = lane if i == 0 else claim()

    return {"nodes": nodes, "edges": edges}
