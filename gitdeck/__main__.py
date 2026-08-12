"""Command-line interface: ``python -m gitdeck <command> ...``.

A thin, scriptable front-end over the gitdeck library.  Any failure is reported
as ``error: <message>`` on stderr with a clean exit code 1 -- never a traceback.
"""

from __future__ import annotations

import argparse
import sys

from . import (
    GitDeckError,
    init_repo,
    status,
    current_branch,
    log,
    file_diff,
    commit_diff,
    stage,
    unstage,
    commit,
    branches,
    create_branch,
    checkout,
    delete_branch,
    merge,
    remotes,
    add_remote,
)


def cmd_init(args):
    path = init_repo(args.path)
    print(f"Initialised empty Git repository in {path}")


def cmd_status(args):
    snap = status(args.path)
    branch = snap.branch or "(no branch)"
    print(f"On branch {branch}")
    if not (snap.staged or snap.unstaged or snap.untracked):
        print("nothing to commit, working tree clean")
        return
    if snap.staged:
        print("\nChanges to be committed:")
        for f in snap.staged_detail["add"]:
            print(f"    new file:   {f}")
        for f in snap.staged_detail["modify"]:
            print(f"    modified:   {f}")
        for f in snap.staged_detail["delete"]:
            print(f"    deleted:    {f}")
    if snap.unstaged:
        print("\nChanges not staged for commit:")
        for f in snap.unstaged:
            print(f"    modified:   {f}")
    if snap.untracked:
        print("\nUntracked files:")
        for f in snap.untracked:
            print(f"    {f}")


def cmd_log(args):
    entries = log(args.path, max=args.max)
    if not entries:
        print("(no commits yet)")
        return
    for e in entries:
        print(f"commit {e['sha']}")
        print(f"Author: {e['author']}")
        print(f"Date:   {e['date']}")
        print()
        for line in e["message"].splitlines() or [""]:
            print(f"    {line}")
        print()


def cmd_diff(args):
    if args.file:
        text = file_diff(args.path, args.file, staged=args.staged)
    elif args.commit:
        text = commit_diff(args.path, args.commit)
    else:
        # whole working tree (or index) -- diff each changed file
        snap = status(args.path)
        files = snap.staged if args.staged else (snap.unstaged or snap.staged)
        text = "".join(
            file_diff(args.path, f, staged=args.staged) for f in files
        )
    sys.stdout.write(text)
    if text and not text.endswith("\n"):
        sys.stdout.write("\n")
    if not text:
        print("(no changes)")


def cmd_stage(args):
    stage(args.path, args.files)
    print(f"Staged {len(args.files)} path(s).")


def cmd_unstage(args):
    unstage(args.path, args.files)
    print(f"Unstaged {len(args.files)} path(s).")


def cmd_commit(args):
    sha = commit(args.path, args.message, author=args.author)
    print(f"[{current_branch(args.path) or 'detached'} {sha[:7]}] {args.message}")


def cmd_branch(args):
    action = args.branch_action
    if action in (None, "list"):
        current = current_branch(args.path)
        for name in branches(args.path):
            marker = "* " if name == current else "  "
            print(f"{marker}{name}")
    elif action == "create":
        create_branch(args.path, args.name, base=args.base)
        print(f"Created branch {args.name}")
    elif action == "checkout":
        checkout(args.path, args.name, force=args.force)
        print(f"Switched to branch {args.name}")
    elif action == "delete":
        delete_branch(args.path, args.name)
        print(f"Deleted branch {args.name}")


def cmd_merge(args):
    result = merge(args.path, args.branch, author=args.author)
    if result.status == "up_to_date":
        print("Already up to date.")
    elif result.status == "merged":
        print(f"Merged {args.branch} ({result.sha[:7]}).")
    elif result.status == "conflict":
        print("Merge conflict in:", file=sys.stderr)
        for f in result.conflicts:
            print(f"    {f}", file=sys.stderr)
        print(
            "Resolve the conflicts, stage the files and commit to finish the merge.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_remote(args):
    action = args.remote_action
    if action in (None, "list"):
        for r in remotes(args.path):
            print(f"{r['name']}\t{r['url']}")
    elif action == "add":
        add_remote(args.path, args.name, args.url)
        print(f"Added remote {args.name} -> {args.url}")


def build_parser():
    p = argparse.ArgumentParser(
        prog="gitdeck", description="GitDeck -- a friendly command-line Git client."
    )
    p.add_argument(
        "-C",
        "--path",
        default=".",
        help="repository folder to operate on (default: current directory)",
    )
    sub = p.add_subparsers(dest="command")

    sp = sub.add_parser("init", help="create an empty repository")
    sp.add_argument("init_dir", nargs="?", default=None, metavar="dir")
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("status", help="show the working-tree status")
    sp.set_defaults(func=cmd_status)

    sp = sub.add_parser("log", help="show commit history")
    sp.add_argument("-n", "--max", type=int, default=100, help="max commits")
    sp.set_defaults(func=cmd_log)

    sp = sub.add_parser("diff", help="show changes")
    sp.add_argument("file", nargs="?", default=None, help="limit to one file")
    sp.add_argument("--staged", action="store_true", help="show staged changes")
    sp.add_argument("--commit", default=None, help="show a commit's diff")
    sp.set_defaults(func=cmd_diff)

    sp = sub.add_parser("stage", help="stage files")
    sp.add_argument("files", nargs="+")
    sp.set_defaults(func=cmd_stage)

    sp = sub.add_parser("unstage", help="unstage files")
    sp.add_argument("files", nargs="+")
    sp.set_defaults(func=cmd_unstage)

    sp = sub.add_parser("commit", help="commit the staged snapshot")
    sp.add_argument("-m", "--message", required=True)
    sp.add_argument("--author", default=None, help='"Name <email>"')
    sp.set_defaults(func=cmd_commit)

    sp = sub.add_parser("branch", help="manage branches")
    bsub = sp.add_subparsers(dest="branch_action")
    bsub.add_parser("list", help="list branches")
    bc = bsub.add_parser("create", help="create a branch")
    bc.add_argument("name")
    bc.add_argument("--base", default=None)
    bco = bsub.add_parser("checkout", help="switch branch")
    bco.add_argument("name")
    bco.add_argument("--force", action="store_true")
    bd = bsub.add_parser("delete", help="delete a branch")
    bd.add_argument("name")
    sp.set_defaults(func=cmd_branch)

    sp = sub.add_parser("merge", help="merge a branch into the current one")
    sp.add_argument("branch")
    sp.add_argument("--author", default=None, help='"Name <email>"')
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("remote", help="manage remotes")
    rsub = sp.add_subparsers(dest="remote_action")
    rsub.add_parser("list", help="list remotes")
    ra = rsub.add_parser("add", help="add a remote")
    ra.add_argument("name")
    ra.add_argument("url")
    sp.set_defaults(func=cmd_remote)

    return p


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 0
    # `init` accepts an optional positional dir that overrides -C/--path.
    if args.command == "init" and getattr(args, "init_dir", None):
        args.path = args.init_dir
    try:
        rc = args.func(args)
    except GitDeckError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return rc if isinstance(rc, int) else 0


if __name__ == "__main__":
    sys.exit(main())
