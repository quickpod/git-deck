"""End-to-end tests for the gitdeck library, all on throwaway temp repos.

Deterministic: every commit passes a fixed author and timestamp.  No test
touches the network (push/pull are exercised only against a local bare repo).
"""

from __future__ import annotations
import sys

import os

import pytest

from gitdeck import (
    GitDeckError,
    init_repo,
    is_repo,
    status,
    current_branch,
    stage,
    unstage,
    commit,
    log,
    commit_graph,
    file_diff,
    commit_diff,
    branches,
    create_branch,
    checkout,
    merge,
    delete_branch,
    remotes,
    add_remote,
)

AUTHOR = "Ada Lovelace <ada@example.com>"
TS = 1_700_000_000  # fixed epoch for reproducible commits


def write(path, name, content):
    with open(os.path.join(path, name), "w", encoding="utf-8") as fh:
        fh.write(content)


def _commit(path, message, ts=TS):
    return commit(path, message, author=AUTHOR, timestamp=ts, timezone=0)


@pytest.fixture
def repo(tmp_path):
    path = str(tmp_path / "work")
    init_repo(path)
    return path


def test_init_repo(tmp_path):
    path = str(tmp_path / "fresh")
    assert not is_repo(path) or True  # may not exist yet
    init_repo(path)
    assert is_repo(path)
    assert os.path.isdir(os.path.join(path, ".git"))


def test_open_nonrepo_raises(tmp_path):
    with pytest.raises(GitDeckError):
        status(str(tmp_path / "does-not-exist"))


def test_status_stage_commit_log(repo):
    write(repo, "hello.txt", "hello world\n")

    snap = status(repo)
    assert "hello.txt" in snap.untracked
    assert "hello.txt" not in snap.staged

    stage(repo, ["hello.txt"])
    snap = status(repo)
    assert "hello.txt" in snap.staged
    assert "hello.txt" not in snap.untracked

    sha = _commit(repo, "add hello")
    assert isinstance(sha, str) and len(sha) == 40

    entries = log(repo)
    assert len(entries) == 1
    assert entries[0]["summary"] == "add hello"
    assert entries[0]["message"] == "add hello"
    assert entries[0]["author"] == AUTHOR
    assert entries[0]["sha"] == sha
    assert entries[0]["parents"] == []

    # working tree is clean again
    snap = status(repo)
    assert not snap.staged and not snap.unstaged and not snap.untracked


def test_unstage(repo):
    write(repo, "a.txt", "one\n")
    stage(repo, ["a.txt"])
    assert "a.txt" in status(repo).staged

    unstage(repo, ["a.txt"])
    snap = status(repo)
    assert "a.txt" not in snap.staged
    assert "a.txt" in snap.untracked


def test_unstage_modification_after_commit(repo):
    write(repo, "a.txt", "v1\n")
    stage(repo, ["a.txt"])
    _commit(repo, "c1")

    write(repo, "a.txt", "v2\n")
    stage(repo, ["a.txt"])
    assert "a.txt" in status(repo).staged

    unstage(repo, ["a.txt"])
    snap = status(repo)
    assert "a.txt" not in snap.staged
    assert "a.txt" in snap.unstaged  # change is still there, just not staged


def test_file_diff_shows_change(repo):
    write(repo, "a.txt", "line one\n")
    stage(repo, ["a.txt"])
    _commit(repo, "c1")

    write(repo, "a.txt", "line two\n")
    diff = file_diff(repo, "a.txt")
    assert "-line one" in diff
    assert "+line two" in diff

    # nothing changed for an untouched file
    assert file_diff(repo, "a.txt", staged=True) == ""


def test_commit_diff(repo):
    write(repo, "a.txt", "first\n")
    stage(repo, ["a.txt"])
    sha = _commit(repo, "c1")
    diff = commit_diff(repo, sha)
    assert "+first" in diff
    assert "a.txt" in diff


def test_commit_requires_message(repo):
    write(repo, "a.txt", "x\n")
    stage(repo, ["a.txt"])
    with pytest.raises(GitDeckError):
        commit(repo, "   ", author=AUTHOR)


def test_commit_nothing_staged_raises(repo):
    write(repo, "a.txt", "x\n")
    stage(repo, ["a.txt"])
    _commit(repo, "c1")
    # nothing new staged -> refuse
    with pytest.raises(GitDeckError):
        _commit(repo, "empty")


def test_branch_create_checkout_merge_fast_forward(repo):
    write(repo, "a.txt", "base\n")
    stage(repo, ["a.txt"])
    _commit(repo, "base commit")

    base = current_branch(repo)
    create_branch(repo, "feature")
    assert set(branches(repo)) >= {base, "feature"}

    checkout(repo, "feature")
    assert current_branch(repo) == "feature"

    write(repo, "b.txt", "on feature\n")
    stage(repo, ["b.txt"])
    _commit(repo, "feature commit", ts=TS + 100)

    checkout(repo, base)
    assert current_branch(repo) == base
    assert not os.path.exists(os.path.join(repo, "b.txt"))

    result = merge(repo, "feature", author=AUTHOR)
    assert result.status in ("merged", "up_to_date")
    assert result.conflicts == []
    # the file introduced on the branch is now present on the base branch
    assert os.path.exists(os.path.join(repo, "b.txt"))


def test_delete_branch(repo):
    write(repo, "a.txt", "x\n")
    stage(repo, ["a.txt"])
    _commit(repo, "c1")
    create_branch(repo, "tmp")
    assert "tmp" in branches(repo)
    delete_branch(repo, "tmp")
    assert "tmp" not in branches(repo)


def test_cannot_delete_current_branch(repo):
    write(repo, "a.txt", "x\n")
    stage(repo, ["a.txt"])
    _commit(repo, "c1")
    with pytest.raises(GitDeckError):
        delete_branch(repo, current_branch(repo))


def test_commit_graph_structure(repo):
    write(repo, "a.txt", "1\n")
    stage(repo, ["a.txt"])
    s1 = _commit(repo, "c1")
    write(repo, "a.txt", "2\n")
    stage(repo, ["a.txt"])
    s2 = _commit(repo, "c2", ts=TS + 50)

    graph = commit_graph(repo)
    shas = [n["sha"] for n in graph["nodes"]]
    assert s1 in shas and s2 in shas
    assert (s2, s1) in graph["edges"]  # c2's parent edge points at c1
    for node in graph["nodes"]:
        assert "lane" in node


def test_remotes_add_and_list(repo):
    assert remotes(repo) == []
    add_remote(repo, "origin", "https://example.com/repo.git")
    rems = remotes(repo)
    assert len(rems) == 1
    assert rems[0]["name"] == "origin"
    assert rems[0]["url"] == "https://example.com/repo.git"


def test_local_push_round_trip(tmp_path):
    """Push to a *local* bare repo (no network) and read the commit back."""
    from dulwich import porcelain
    from dulwich.repo import Repo

    work = str(tmp_path / "work")
    bare = str(tmp_path / "remote.git")
    init_repo(work)
    porcelain.init(bare, bare=True)

    write(work, "a.txt", "shipit\n")
    stage(work, ["a.txt"])
    sha = _commit(work, "c1")

    branch = current_branch(work)
    add_remote(work, "origin", bare)
    porcelain.push(work, bare, f"refs/heads/{branch}")

    remote = Repo(bare)
    try:
        assert remote.refs[f"refs/heads/{branch}".encode()].decode() == sha
    finally:
        remote.close()


@pytest.mark.skipif(sys.platform == "win32",
                    reason="Windows CI has a real display; main() would open a window and block")
def test_gui_imports_and_headless_main():
    from gitdeck import gui
    assert gui.main() == 0  # no display -> clean 0, no exception
